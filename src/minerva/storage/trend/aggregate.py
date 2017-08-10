# -*- coding: utf-8 -*-
"""
Formula aggregation module
"""
import time
import datetime
import logging
from functools import partial
from contextlib import closing
from operator import itemgetter

import pytz

from pyparsing import Word, nums, alphas, alphanums, Combine, oneOf, \
    Optional, opAssoc, operatorPrecedence, Suppress, delimitedList, Forward, \
    QuotedString

from minerva.db.query import Table
from minerva.storage.trend.tables import PARTITION_SIZES


def get_aggregate_shard(
        conn, entities_sql, entity_type_id, granularity, formula_str,
        shard_index):
    start, end = shard_interval(granularity, shard_index)

    return get_aggregate_data(
        conn, entities_sql, entity_type_id, granularity,
        formula_str, start, end
    )


def get_aggregate_data(
        conn, entities_query, entity_type_id, granularity, formula_str, start,
        end):
    formula = arith_expr.parseString(formula_str, parseAll=True)[0]

    get_tm = partial(get_trend_meta, conn, entity_type_id, granularity)

    context = Context(get_tm)

    formula_sql = formula(context)

    tables = map(partial(Table, "trend"), list(context.tables))

    first_partition_table = tables[0]

    first_trend_join = (
        'JOIN {0} '
        'ON {0}.entity_id = e.id'
    ).format(first_partition_table.render())

    trend_joins = [first_trend_join]

    for partition_table in tables[1:]:
        trend_join = (
            'JOIN {0} '
            'ON {0}.entity_id = e.id '
            'AND {0}.timestamp = {1}.timestamp'
        ).format(partition_table.render(), first_partition_table.render())

        trend_joins.append(trend_join)

    sql = (
        'SELECT {0}.timestamp, {1} '
        'FROM ({2}) e '
        '{3} '
        'WHERE {0}.timestamp > %(start)s AND {0}.timestamp <= %(end)s '
        'GROUP BY {0}.timestamp'
    ).format(
        first_partition_table.render(), formula_sql, entities_query,
        " ".join(trend_joins)
    )

    args = {'start': start, 'end': end}

    with closing(conn.cursor()) as cursor:
        cursor.execute(sql, args)
        rows = cursor.fetchall()

    return sorted(rows, key=itemgetter(0))


def get_trend_meta(
        conn, entity_type_id, granularity, data_source_name, trend_name):
    """
    Return tuple (datasource_name, trend_name, table_name)
    """
    criteria = [
        "ts.entity_type_id = %s",
        "ts.granularity = %s",
        "trend.name = %s"
    ]

    criteria_args = [entity_type_id, granularity.name, trend_name]

    if data_source_name:
        criteria.append('lower(d.name) = lower(%s)')
        criteria_args.append(data_source_name)

    sql = (
        "SELECT d.name, trend.name, trend_directory.base_table_name(ts) "
        "FROM trend_directory.trend "
        "JOIN trend_directory.trend_store ts "
        "ON ts.id = trend.trend_store_id "
        "JOIN directory.data_source d "
        "ON d.id = ts.data_source_id "
        "WHERE {}"
    ).format(" AND ".join(criteria))

    args = tuple(criteria_args)

    with closing(conn.cursor()) as cursor:
        cursor.execute(sql, args)

        return cursor.fetchone()


def eval_identifier(tokens):
    if len(tokens) > 1:
        namespace = tokens[0]
        name = tokens[1]
    else:
        namespace = None
        name = tokens[0]

    def eval(context):
        return context.get_trend_column(namespace, name)

    return eval


def eval_integer(tokens):
    value = tokens[0]

    def eval(context):
        return value

    return eval


def eval_real(tokens):
    value = tokens[0]

    def eval(context):
        return value

    return eval


def eval_signop(tokens):
    sign, value = tokens[0]

    def eval(context):
        return "{}{}".format(sign, value(context))

    return eval


def operator_operands(tokenlist):
    """generator to extract operators and operands in pairs"""
    it = iter(tokenlist)

    return zip(it, it)


def eval_multop(tokens):
    value = tokens[0]

    def eval(context):
        result = value[0](context)

        for op, val in operator_operands(value[1:]):
            result += " {} {}".format(op, val(context))

        return result

    return eval


def eval_addop(tokens):
    value = tokens[0]

    def eval(context):
        result = value[0](context)

        for op, val in operator_operands(value[1:]):
            result += " {} {}".format(op, val(context))

        return result

    return eval


def eval_fn_call(tokens):
    fn_name = tokens[0].upper()
    arg_tokens = tokens[1:]

    def eval(context):
        args = [arg(context) for arg in arg_tokens]

        return "{}({})".format(fn_name, ", ".join(args))

    return eval


def eval_brackets(tokens):
    expr = tokens[0]

    def eval(context):
        return "(" + expr(context) + ")"

    return eval


integer = Word(nums)
real = (
        Combine(Word(nums) + Optional("." + Word(nums)) + oneOf(
            "E e") + Optional(oneOf('+ -')) + Word(nums)) | Combine(Word(
                nums) + "." + Word(nums))
    )

identifier_part = Word(alphanums + '_') | QuotedString(
        '"', unquoteResults=True)

identifier = Optional(
    identifier_part + Suppress(".")
) + identifier_part

funcName = Word(alphas)

lpar = Suppress('(')
rpar = Suppress(')')

arith_expr = Forward()

brackets = (lpar + arith_expr + rpar)
brackets.setParseAction(eval_brackets)

fnCall = (funcName + lpar + delimitedList(arith_expr) + rpar)
fnCall.setParseAction(eval_fn_call)

operand = real | integer | brackets | fnCall | identifier

signop = oneOf('+ -')
multop = oneOf('* / // %')
plusop = oneOf('+ -')

real.setParseAction(eval_real)
integer.setParseAction(eval_integer)
identifier.setParseAction(eval_identifier)

arith_expr << operatorPrecedence(operand, [
    (signop, 1, opAssoc.RIGHT, eval_signop),
    (multop, 2, opAssoc.LEFT, eval_multop),
    (plusop, 2, opAssoc.LEFT, eval_addop)])


class Context:
    def __init__(self, get_trend_meta):
        self.trends = set()
        self.tables = set()
        self.table_by_trend = {}
        self.get_trend_meta = get_trend_meta

    def get_trend_column(self, data_source_name, trend_name):
        trend_meta = self.get_trend_meta(data_source_name, trend_name)

        data_source_name, trend_name, table_name = trend_meta

        trend_ident = (data_source_name, trend_name)

        logging.debug("meta: {}".format(trend_meta))

        if trend_ident not in self.trends:
            self.trends.add(trend_ident)
            self.table_by_trend[trend_ident] = table_name

        self.tables.add(table_name)

        return '"{}"."{}"'.format(table_name, trend_name)


def shard_interval(granularity, shard_index):
    shard_size = PARTITION_SIZES[str(granularity)]

    timetuple = time.gmtime(shard_index * shard_size)

    start = pytz.UTC.localize(datetime.datetime(*timetuple[:6]))
    end = start + datetime.timedelta(seconds=shard_size)

    return start, end


EPOCH = datetime.datetime(1970, 1, 1, 0, 0, 0)


def get_partition_timestamp_for(timezone, partition_size, timestamp):
    if timestamp.tzinfo is None:
        raise TypeError("Timestamp has no timezone information")

    partition_delta = datetime.timedelta(0, partition_size)

    localized_timestamp = timestamp.astimezone(timezone)

    ref = offset_hack(timezone.localize(EPOCH))

    # Deal with missing hour in case of summer time
    utc_offset_delta = localized_timestamp.utcoffset() - ref.utcoffset()

    timestamp_offset = localized_timestamp - ref + utc_offset_delta

    partition_index = int(timestamp_offset.total_seconds() / partition_size)

    partition_timestamp = ref + (partition_index * partition_delta)

    return sanitize_timestamp(partition_timestamp)


def sanitize_timestamp(timestamp):
    timetuple = timestamp.timetuple()

    naive_timestamp = datetime.datetime(*timetuple[:6])

    return timestamp.tzinfo.localize(naive_timestamp)


def offset_hack(ref):
    # Get right offset (backward compatibility)
    if ref.utcoffset().total_seconds() > 0:
        ref += datetime.timedelta(1)

    return ref.replace(hour=0, minute=0)


def timestamp_range(start, end, step):
    timestamp = start

    while timestamp <= end:
        yield timestamp

        timestamp += step


def partitions_for_period(timezone, partition_size, start, end):
    partition_delta = datetime.timedelta(seconds=partition_size)

    timestamp_to_partition_timestamp = partial(
        get_partition_timestamp_for, timezone, partition_size
    )

    timestamps = timestamp_range(start, end, partition_delta)

    return map(timestamp_to_partition_timestamp, timestamps)


def compile_list_sql(_conn, entity_ids):
    return (
        "SELECT id, dn, entitytype_id "
        "FROM directory.entity "
        "WHERE id IN ({})"
    ).format(",".join(map(str, entity_ids)))


def compile_minerva_query_sql(conn, minerva_query):
    sql_minerva_query = build_sql_minerva_query(minerva_query)

    query = "SELECT directory.compile_minerva_query({})".format(
        sql_minerva_query)

    with closing(conn.cursor()) as cursor:
        cursor.execute(query)

        sql, = cursor.fetchone()

    return sql


def build_sql_minerva_query(minerva_query):
    sql_minerva_query_parts = map(cs_to_sql, iter_cs(minerva_query))

    return "ARRAY[{}]::directory.query_part[]".format(
        ",".join(sql_minerva_query_parts)
    )


def cs_to_sql(cs):
    c, s = cs

    c_parts = ",".join("'{}'".format(tag) for tag in c)

    if s is None:
        s_part = "NULL"
    else:
        s_part = "'{}'".format(s)

    return "(ARRAY[{}]::text[], {})".format(c_parts, s_part)


def iter_cs(query):
    full_pair_count, remainder = divmod(len(query), 2)

    for i in range(full_pair_count):
        yield query[i * 2]["value"], query[(i * 2) + 1]["value"]

    if remainder > 0:
        yield query[-1]["value"], None
