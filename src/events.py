import asyncio
import argparse
import os
import time
from pathlib import Path
import random
from typing import List, Dict, AsyncGenerator, Any
import logging.config

import asyncpg
from fastapi import FastAPI
from faker import Faker
from faker.providers import credit_card, date_time, geo, person, misc
import geopandas as gpd
from shapely.geometry import Point

ip_address = os.environ.get('IP')

app = FastAPI()

POSTGRES_URI = os.environ.get("POSTGRES_URI", f"postgresql://postgres:postgres@{ip_address}:5432")
POSTGRES_TABLE_NAME = os.environ.get("POSTGRES_TABLE_NAME", "public.credit_events")

KAFKA_BROKER_URL = os.environ.get("KAFKA_BROKER_URL", f"PLAINTEXT://localhost:9092")
KAFKA_TOPIC_NAME = os.environ.get("KAFKA_TOPIC_NAME", "com.miserani.events")

fake = Faker('pt_BR')
fake.add_provider(credit_card)
fake.add_provider(date_time)
fake.add_provider(geo)
fake.add_provider(person)
fake.add_provider(misc)

world = gpd.read_file(gpd.datasets.get_path('naturalearth_lowres'))
brazil = world[world['name'] == "Brazil"].geometry.iloc[0]

# Configurar o logger usando basicConfig
logging.config.fileConfig(f"{Path(__file__).parents[0]}/logging.ini")
logger = logging.getLogger(__name__)


class EventMetrics:
    def __init__(self, num_vthreads):
        self.event_count = 0  # Número total de eventos gerados
        self.num_vthreads = num_vthreads
        self.start_time = time.time()
        self.log_interval = 0.0  # Intervalo de registro em segundos (por minuto)
        self.last_log_time = self.start_time

    def increment_event_count(self):
        self.event_count += 1
        current_time = time.time()
        if current_time - self.last_log_time >= self.log_interval:
            self.log_metrics()
            self.last_log_time = current_time

    def calculate_arrival_rate(self):
        current_time = time.time()
        elapsed_time = current_time - self.start_time
        arrival_rate = (self.event_count * self.num_vthreads) / elapsed_time
        # arrival_rate = self.event_count / elapsed_time
        return arrival_rate / 60

    def log_metrics(self):
        arrival_rate = self.calculate_arrival_rate()
        logging.info(f"Arrival Rate (λ): {arrival_rate:.2f} events per second")
        logging.info(f"Total Number of Events: {self.event_count * self.num_vthreads}")


def generate_cpf_pool(size=100):
    repetitions = random.randint(5, 10)
    if size < repetitions:
        size = repetitions
    unique_cpfs = [fake.random_number(digits=11, fix_len=True) for _ in range(size // repetitions)]

    cpfs = unique_cpfs.copy()
    for _ in range(size - len(unique_cpfs)):
        cpfs.append(unique_cpfs[random.randint(0, len(unique_cpfs) - 1)])

    random.shuffle(cpfs)
    return cpfs


def generate_lat_long_within_brazil():
    while True:
        lat_limits = (-33.0, 5.0)  # Latitude Sul e Norte
        long_limits = (-74.0, -35.0)  # Longitude Oeste e Leste
        lat = random.uniform(*lat_limits)
        long = random.uniform(*long_limits)
        point = Point(long, lat)
        if brazil.contains(point):
            return lat, long


async def generate_records(num_records: int) -> AsyncGenerator[Dict, None]:
    cpf_pool_ = generate_cpf_pool(size=num_records)
    for _ in range(num_records):
        datetime_ = fake.date_time_between(start_date='-90d', end_date='now').replace(microsecond=0)
        lat, long = generate_lat_long_within_brazil()
        record = {
            "cpf": int(cpf_pool_[_]),
            "cc_num": int(fake.credit_card_number(card_type="mastercard")),
            "first": fake.first_name(),
            "last": fake.last_name(),
            "trans_num": int(fake.random_number(digits=12, fix_len=True)),
            "trans_date": datetime_,
            "trans_time": datetime_,
            "unix_time": fake.unix_time(),
            "category": fake.random_element(elements=('grocery', 'entertainment', 'utility', 'rent', 'gas', 'dining',
                                                      'travel', 'clothing', 'misc_pos', 'grocery_pos', 'shopping_pos',
                                                      'food_pos', 'misc_net', 'grocery_net', 'shopping_net', 'food_net')
                                            ),
            "merchant": fake.company(),
            "value": fake.random_number(digits=4, fix_len=True) / 100,
            "lat": str(lat),
            "long": str(long)
        }
        yield record


async def insert_data_postgres(record: Dict) -> None:
    conn = await asyncpg.connect(POSTGRES_URI)
    await conn.execute("""
        INSERT INTO {} (cpf, cc_num, first, last, trans_num, trans_date, trans_time, unix_time, category, merchant, 
        value, lon, lat, location)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, ST_GeomFromText($14, 4326));
    """.format(POSTGRES_TABLE_NAME), (
        record["cpf"], record["cc_num"], record["first"], record["last"], record["trans_num"],
        record["trans_date"], record["trans_time"], record["unix_time"], record["category"],
        record["merchant"], record["value"], record['long'], record['lat'],
        f"POINT({record['long']} {record['lat']})",
    ))
    await conn.close()


async def publish_to_kafka(record: Dict) -> None:
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers="localhost")
    await producer.start()

    def convert_datetime_to_str(records):
        import datetime
        for key, value in records.items():
            if isinstance(value, datetime.datetime):
                records[key] = value.strftime('%Y-%m-%d %H:%M:%S')
        return records

    await producer.send_and_wait(
        KAFKA_TOPIC_NAME,
        key=str(record["cpf"]).encode('utf-8'),
        value=str(convert_datetime_to_str(record)).encode('utf-8')
    )
    await producer.stop()


def run_server():
    try:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt as e:
        logger.info("Shutting down server")


async def async_worker(num_records, db_type, metrics):
    if db_type == "postgres":
        async for record in generate_records(num_records):
            await insert_data_postgres(record)
            metrics.increment_event_count()
    elif db_type == "kafka":
        async for record in generate_records(num_records):
            await publish_to_kafka(record)
            metrics.increment_event_count()


@app.get("/events", response_model=List[Dict])
async def get_events(num_records: int = 10) -> List[Dict]:
    records = [record async for record in generate_records(num_records)]
    return records


async def run_tasks(args):
    logger.info("Starting data generation...")
    if args.db == "API":
        run_server()
    else:
        tasks = []
        for _ in range(args.vthreads):
            metrics = EventMetrics(args.vthreads)
            task = asyncio.create_task(async_worker(args.records, args.db, metrics))
            tasks.append(task)
        await asyncio.gather(*tasks)

    logger.info("Data generation completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Insert fake data into MongoDB or PostgreSQL and publish to Kafka or return events API.")
    parser.add_argument("--db", choices=["postgres", "kafka", "API"], required=True,
                        help="Events create type (postgres, kafka or API)")
    parser.add_argument("--vthreads", type=int, default=1, help="Number of threads")
    parser.add_argument("--records", type=int, default=1000, help="Records per thread")
    args = parser.parse_args()

    asyncio.run(run_tasks(args))