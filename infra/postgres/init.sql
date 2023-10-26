CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    first VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE events (
    id SERIAL PRIMARY KEY,
    cpf BIGINT NOT NULL,
    cc_num BIGINT NOT NULL,
    first VARCHAR(255) NOT NULL,
    last VARCHAR(255) NOT NULL,
    trans_num BIGINT NOT NULL,
    trans_date TIMESTAMP NOT NULL,
    trans_time TIMESTAMP NOT NULL,
    unix_time BIGINT NOT NULL,
    category VARCHAR(255) NOT NULL,
    merchant VARCHAR(255) NOT NULL,
    value DECIMAL(10, 2) NOT NULL,
    location GEOMETRY(Point, 4326),  -- Armazena lat/lon como um ponto em um sistema de coordenadas geográficas (SRID 4326)
    lon VARCHAR(255) NOT NULL,
    lat VARCHAR(255) NOT NULL
);

CREATE INDEX idx_events_trans_date ON events (trans_date);