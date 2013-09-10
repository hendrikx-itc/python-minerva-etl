import os
import logging
from contextlib import closing
from functools import wraps

import psycopg2.extras

from minerva.util.debug import log_call_basic
from minerva.db import parse_db_url, extract_safe_url


def connect():
    db_url = os.getenv("TEST_DB_URL")

    if db_url is None:
        raise Exception("Environment variable TEST_DB_URL not set")

    scheme, user, password, host, port, database = parse_db_url(db_url)

    if scheme != "postgresql":
        raise Exception("Only PostgreSQL connections are supported")

    conn = psycopg2.connect(
        database=database, user=user, password=password,
        host=host, port=port,
        connection_factory=psycopg2.extras.LoggingConnection)

    logging.info("connected to {}".format(extract_safe_url(db_url)))

    conn.initialize(logging.getLogger(""))

    conn.commit = log_call_basic(conn.commit)
    conn.rollback = log_call_basic(conn.rollback)

    return conn


def with_conn(*setup_functions):
    def dec_fn(f):
        """
        Decorator for functions that require a database connection:

        @with_conn
        def somefunction(conn):
            ...
        """
        @wraps(f)
        def wrapper(*args, **kwargs):
            with closing(connect()) as conn:
                for setup_fn in setup_functions:
                    setup_fn(conn)

                return f(conn, *args, **kwargs)

        return wrapper

    return dec_fn


def with_dataset(dataset):
    def dec_fn(f):
        @wraps(f)
        def wrapper(conn, *args, **kwargs):
            with closing(conn.cursor()) as cursor:
                d = dataset()
                d.load(cursor)

            conn.commit()

            return f(conn, d, *args, **kwargs)

        return wrapper

    return dec_fn
