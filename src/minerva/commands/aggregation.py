from typing import Dict, List
import os
import json
import re
from itertools import chain
from collections import OrderedDict

import yaml

from minerva.instance import MinervaInstance, TrendStore, TrendStorePart, Trend, GeneratedTrend
from minerva.commands import ConfigurationError


def setup_command_parser(subparsers):
    cmd = subparsers.add_parser(
        'aggregation', help='commands for defining aggregations'
    )

    cmd_subparsers = cmd.add_subparsers()

    setup_time_parser(cmd_subparsers)
    setup_entity_parser(cmd_subparsers)


def setup_entity_parser(subparsers):
    cmd = subparsers.add_parser(
        'entity', help='define entity aggregation'
    )

    cmd.add_argument(
        '--format', choices=['yaml', 'json'], default='yaml',
        help='format of definition'
    )

    cmd.add_argument(
        'definition',
        help='Aggregations definition file'
    )

    cmd.set_defaults(cmd=entity_aggregation)


class AggregationContext:
    instance: MinervaInstance
    source_definition: TrendStore

    def __init__(self, instance, aggregation_file_path, file_format):
        self.instance = instance
        self.aggregation_file_path = aggregation_file_path

        with open(aggregation_file_path) as definition_file:
            if file_format == 'json':
                definition = json.load(definition_file)
            elif file_format == 'yaml':
                definition = ordered_load(definition_file, Loader=yaml.SafeLoader)

        self.definition = definition

        self.source_definition_file_path = None
        self.source_definition = None

        self.load_source_definition()

        self.configuration_check()

    def load_source_definition(self):
        raise NotImplementedError()

    def configuration_check(self):
        raise NotImplementedError()

    def generated_file_header(self):
        """
        Return a string that can be placed at the start of generated files as header
        """
        relative_aggregation_file_path = self.instance.make_relative(
            self.aggregation_file_path
        )

        relative_source_definition_path = self.instance.make_relative(
            self.definition['entity_aggregation']['source']
        )

        return (
            '###########################################################################\n'
            '#\n'
            '# This file is automatically generated by the `minerva aggregation` command\n'
            '#\n'
            f'# definition:         {relative_aggregation_file_path}\n'
            f'# source trend store: {relative_source_definition_path}\n'
            '#\n'
            '###########################################################################\n'
        )


class EntityAggregationContext(AggregationContext):
    def load_source_definition(self):
        self.source_definition = self.instance.load_trend_store(
            self.definition['entity_aggregation']['source']
        )

    def configuration_check(self):
        # Check if the relation matches the aggregation
        relation = load_relation(
            self.instance.root, self.definition['entity_aggregation']['relation']
        )

        if self.definition['entity_aggregation']['entity_type'] != relation['target_entity_type']:
            raise ConfigurationError(
                'Entity type mismatch between definition and relation target: {} != {}'.format(
                    self.definition['entity_aggregation']['entity_type'],
                    relation['target_entity_type']
                )
            )


class AggregationPart:
    name: str
    source: str


class TimeAggregation:
    source: TrendStore
    name: str
    data_source: str
    entity_type: str
    granularity: str
    mapping_function: str
    parts: List[AggregationPart]

    def __init__(self):
        self.source = None
        self.name = None
        self.data_source = None
        self.entity_type = None
        self.granularity = None
        self.mapping_function = None
        self.parts = []


class TimeAggregationContext(AggregationContext):
    def load_source_definition(self):
        self.source_definition = self.instance.load_trend_store(
            self.definition['time_aggregation']['source']
        )

    def configuration_check(self):
        pass


def entity_aggregation(args):
    instance = MinervaInstance.load()

    aggregation_context = EntityAggregationContext(
        instance, args.definition, args.format
    )

    write_entity_aggregations(aggregation_context)

    aggregate_trend_store = define_aggregate_trend_store(
        aggregation_context
    )

    base_name = aggregation_context.definition['entity_aggregation']['name']

    aggregate_trend_store_file_path = aggregation_context.instance.trend_store_file_path(
        base_name
    )

    print("Writing aggregate trend store to '{}'".format(
        aggregate_trend_store_file_path
    ))

    with open(aggregate_trend_store_file_path, 'w') as out_file:
        out_file.write(aggregation_context.generated_file_header())

        ordered_dump(
            aggregate_trend_store.to_json(), stream=out_file, Dumper=yaml.SafeDumper,
            indent=2
        )


def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=OrderedDict):
    class OrderedLoader(Loader):
        pass

    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))

    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)

    return yaml.load(stream, OrderedLoader)


def ordered_dump(data, stream=None, Dumper=yaml.Dumper, **kwds):
    class OrderedDumper(Dumper):
        pass

    def _dict_representer(dumper, data):
        return dumper.represent_mapping(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            data.items()
        )

    OrderedDumper.add_representer(OrderedDict, _dict_representer)
    OrderedDumper.add_representer(SqlSrc, SqlSrc.representer)

    return yaml.dump(data, stream, OrderedDumper, **kwds)


def load_relation(instance_root: str, relation: str) -> Dict:
    """
    :param instance_root:
    :param relation: Can be an absolute path, or a filename (with or without
    extension) relative to relation directory in instance root.
    :return:
    """
    path_variants = [
        relation,
        os.path.join(instance_root, 'relation', relation),
        os.path.join(instance_root, 'relation', '{}.yaml'.format(relation))
    ]

    try:
        yaml_file_path = next(
            path for path in path_variants if os.path.isfile(path)
        )
    except StopIteration:
        raise Exception("No such relation '{}'".format(relation))

    print("Using relation definition '{}'".format(yaml_file_path))

    with open(yaml_file_path) as yaml_file:
        return yaml.load(yaml_file, Loader=yaml.SafeLoader)


def write_entity_aggregations(aggregation_context: EntityAggregationContext) -> None:
    """
    Generate and write aggregations for all parts of the trend store

    :param aggregation_context: Complete context for entity aggregation to write
    :return: None
    """
    definition = aggregation_context.definition['entity_aggregation']

    for part in aggregation_context.source_definition.parts:
        dest_part = next(
            dest_part
            for dest_part in definition['parts']
            if dest_part['source'] == part.name
        )

        aggregation = define_part_entity_aggregation(
            part,
            definition['relation'],
            dest_part['name']
        )

        materialization_file_path = aggregation_context.instance.materialization_file_path(dest_part['name'])

        print("Writing materialization to '{}'".format(materialization_file_path))

        with open(materialization_file_path, 'w') as out_file:
            out_file.write(aggregation_context.generated_file_header())

            ordered_dump(
                aggregation, stream=out_file, Dumper=yaml.SafeDumper,
                indent=2
            )


def define_part_entity_aggregation(part: TrendStorePart, relation, name):
    mapping_function = 'trend.mapping_id'

    return OrderedDict([
        ('target_trend_store_part', name),
        ('enabled', True),
        ('processing_delay', '30m'),
        ('stability_delay', '5m'),
        ('reprocessing_period', '3 days'),
        ('sources', [
            OrderedDict([
                ('trend_store_part', part.name),
                ('mapping_function', mapping_function)
            ])
        ]),
        ('view', SqlSrc(aggregate_view_sql(part, relation))),
        ('fingerprint_function', SqlSrc(define_fingerprint_sql(part)))
    ])


def aggregate_view_sql(part: TrendStorePart, relation) -> str:
    trend_columns = [
        f'  {trend.entity_aggregation}("{trend.name}") AS "{trend.name}"'
        for trend in part.trends
    ]

    query_parts = [
        'SELECT\n',
        '  r.target_id AS entity_id,\n',
        '  timestamp,\n',
        '  count(*) AS samples,\n'
    ]

    query_parts.append(
        ',\n'.join(trend_columns)
    )

    query_parts.extend([
        '\nFROM trend."{}" t\n'.format(part.name),
        'JOIN relation."{}" r ON t.entity_id = r.source_id\n'.format(relation),
        'GROUP BY timestamp, r.target_id;\n'
    ])

    return ''.join(query_parts)


def define_view_materialization_sql(data, name) -> List[str]:
    return [
        'SELECT trend_directory.define_view_materialization(\n',
        "    id, '30m'::interval, '5m'::interval, '3 days'::interval, 'trend._{}'::regclass\n".format(name),
        ')\n',
        'FROM trend_directory.trend_store_part\n',
        "WHERE name = '{}';\n".format(name)
    ]


def define_fingerprint_sql(src_part: TrendStorePart):
    return (
        f'SELECT modified.last, format(\'{{"{src_part.name}": "%s"}}\', modified.last)::jsonb\n'
        'FROM trend_directory.modified\n'
        'JOIN trend_directory.trend_store_part ttsp ON ttsp.id = modified.trend_store_part_id\n'
        f"WHERE ttsp::name = '{src_part.name}' AND modified.timestamp = $1;\n"
    )


def enable_sql(name):
    return [
        "UPDATE trend_directory.materialization SET enabled = true "
        "WHERE materialization::text = '{}';\n".format(name)
    ]


AGGREGATE_DATA_TYPE_MAPPING = {
    'smallint': 'bigint',
    'integer': 'bigint',
    'bigint': 'numeric',
    'float': 'double precision',
    'double precision': 'double precision',
    'real': 'double precision',
    'numeric': 'numeric'
}

PARTITION_SIZE_MAPPING = {
    '15m': '1d',
    '30m': '2d',
    '1h': '4d',
    '1d': '3month',
    '1w': '1y',
    '1month': '5y',
}


def define_aggregate_trend_store(aggregation_context) -> TrendStore:
    if 'entity_aggregation' in aggregation_context.definition:
        definition = aggregation_context.definition['entity_aggregation']
    else:
        definition = aggregation_context.definition['time_aggregation']

    src_trend_store = aggregation_context.source_definition

    if definition.get('data_source') is None:
        target_data_source = src_trend_store.data_source
    else:
        target_data_source = definition.get('data_source')

    if definition.get('granularity') is None:
        target_granularity = src_trend_store.granularity
    else:
        target_granularity = definition.get('granularity')

    if definition.get('entity_type') is None:
        target_entity_type = src_trend_store.entity_type
    else:
        target_entity_type = definition.get('entity_type')

    aggregate_parts = []

    for aggregate_part_def in definition['parts']:
        if 'source' in aggregate_part_def:
            src_part = next(
                part
                for part in src_trend_store.parts
                if part.name == aggregate_part_def['source']
            )

            aggregate_part = define_aggregate_part(src_part, aggregate_part_def)

            aggregate_parts.append(aggregate_part)
        else:
            aggregate_part = TrendStorePart.from_json(aggregate_part_def)

            aggregate_parts.append(aggregate_part)

    return TrendStore(
        target_data_source,
        target_entity_type,
        target_granularity,
        PARTITION_SIZE_MAPPING[target_granularity],
        aggregate_parts
    )


def define_aggregate_part(data: TrendStorePart, definition):
    trends = [
        define_aggregate_trend(trend) for trend in data.trends
    ]

    added_generated_trends = [
        GeneratedTrend(
            generated_trend_def['name'],
            generated_trend_def['data_type'],
            generated_trend_def.get('description'),
            generated_trend_def['expression'],
            generated_trend_def.get('extra_data')
        )
        for generated_trend_def in definition.get('generated_trends', [])
    ]

    generated_trends = list(chain(
        data.generated_trends,
        added_generated_trends
    ))

    # If there is no samples column, we add one
    if not len([trend for trend in trends if trend.name == 'samples']):
        trends.insert(
            0,
            Trend(
                name='samples',
                data_type='smallint',
                description='Number of source records',
                time_aggregation='sum',
                entity_aggregation='sum',
                extra_data={}
            )
        )

    return TrendStorePart(
        definition['name'],
        trends,
        generated_trends
    )


def aggregate_data_type(data_type):
    return AGGREGATE_DATA_TYPE_MAPPING.get(data_type, data_type)


def define_aggregate_trend(data: Trend):
    return Trend(
        data.name,
        aggregate_data_type(data.data_type),
        '',
        data.time_aggregation,
        data.entity_aggregation,
        data.extra_data
    )


def part_name_mapper_entity(
        new_data_source=None, new_entity_type=None, new_granularity=None):
    """
    Map part names by replacing components of the name

    :return: Mapped name
    """
    def map_part_name(name):
        match = re.match('(.*)_(.*)_([1-9][0-8]*[mhdw])', name)

        data_source, entity_type, granularity = match.groups()

        if new_data_source is not None:
            data_source = new_data_source

        if new_entity_type is not None:
            entity_type = new_entity_type

        if new_granularity is not None:
            granularity = new_granularity

        return '{}_{}_{}'.format(data_source, entity_type, granularity)

    return map_part_name


def setup_time_parser(subparsers):
    cmd = subparsers.add_parser(
        'time', help='define time aggregation'
    )

    cmd.add_argument(
        '--format', choices=['yaml', 'json'], default='yaml',
        help='format of definition'
    )

    cmd.add_argument(
        'definition', help='file containing relation definition'
    )

    cmd.set_defaults(cmd=time_aggregation)


def time_aggregation(args):
    instance = MinervaInstance.load()

    aggregation_context = TimeAggregationContext(
        instance, args.definition, args.format
    )

    write_time_aggregations(aggregation_context)

    aggregate_trend_store = define_aggregate_trend_store(aggregation_context)

    aggregate_trend_store_file_path = os.path.join(
        instance.root,
        'trend',
        '{}.yaml'.format(aggregation_context.definition['time_aggregation']['name'])
    )

    print("Writing aggregate trend store to '{}'".format(
        aggregate_trend_store_file_path
    ))

    with open(aggregate_trend_store_file_path, 'w') as out_file:
        ordered_dump(aggregate_trend_store.to_json(), out_file, indent=2)


def part_name_mapper_time(new_suffix):
    """
    Map part names by cutting off the existing granularity suffix and appending
    the new suffix.

    :param new_suffix:
    :return: Mapped name
    """
    def map_part_name(name):
        # Strip existing suffix
        without_suffix = re.sub('_([1-9][0-8]*[mhdw])', '', name)

        return '{}_{}'.format(without_suffix, new_suffix)

    return map_part_name


def write_time_aggregations(
        aggregation_context: TimeAggregationContext
) -> List[str]:
    """
    Define the aggregations for all parts of the trend store

    :param source_definition:
    :param part_name_mapping: A function for mapping part name to aggregate part
     name
    :return: Lines of SQL
    """
    for agg_part in aggregation_context.definition['time_aggregation']['parts']:
        if 'source' in agg_part:
            try:
                source_part = next(
                    part
                    for part in aggregation_context.source_definition.parts
                    if agg_part['source'] == part.name
                )
            except StopIteration:
                raise ConfigurationError(
                    "No source definition found for aggregation part '{}'(source: {})".format(
                        agg_part['name'], agg_part['source']
                    )
                )

            materialization_file_path = aggregation_context.instance.materialization_file_path(agg_part['name'])

            print(
                "Writing materialization to '{}'".format(materialization_file_path)
            )

            mapping_function = aggregation_context.definition['time_aggregation']['mapping_function']
            target_granularity = aggregation_context.definition['time_aggregation']['granularity']

            aggregate_definition = define_part_time_aggregation(
                source_part, aggregation_context.source_definition.granularity, mapping_function,
                target_granularity, agg_part['name']
            )

            with open(materialization_file_path, 'w') as out_file:
                ordered_dump(
                    aggregate_definition, stream=out_file, Dumper=yaml.SafeDumper,
                    indent=2
                )


def define_part_time_aggregation(source_part: TrendStorePart, source_granularity, mapping_function, target_granularity, name) -> OrderedDict:
    """
    Use the source part definition to generate the aggregation SQL.

    :param source_part:
    :param name: Name of the aggregate part use for the function name, etc.
    :return: Lines of SQL
    """
    return OrderedDict([
        ('target_trend_store_part', name),
        ('enabled', True),
        ('processing_delay', '30m'),
        ('stability_delay', '5m'),
        ('reprocessing_period', '3 days'),
        ('sources', [
            OrderedDict([
                ('trend_store_part', source_part.name),
                ('mapping_function', mapping_function)
            ])
        ]),
        ('function', aggregate_function(source_part, target_granularity)),
        ('fingerprint_function', SqlSrc(fingerprint_function_sql(source_part, source_granularity, target_granularity)))
    ])


class SqlSrc(str):
    @staticmethod
    def representer(dumper, data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


def aggregate_function(part_data: TrendStorePart, target_granularity):
    trend_columns = [
        '  "{}" {}'.format(
            trend.name,
            aggregate_data_type(trend.data_type)
        )
        for trend in part_data.trends
    ]

    trend_column_expressions = [
        '      {}(t."{}") AS "{}"'.format(
            trend.time_aggregation,
            trend.name,
            trend.name
        )
        for trend in part_data.trends
    ]

    column_expressions = [
        '      entity_id',
        '      $2 AS timestamp'
    ] + trend_column_expressions

    return_type = (
        'TABLE (\n' +
        '  "entity_id" integer,\n' +
        '  "timestamp" timestamp with time zone,\n' +
        ',\n'.join(trend_columns) +
        '\n' +
        ')\n'
    )

    src = (
        'BEGIN\n' +
        'RETURN QUERY EXECUTE $query$\n' +
        '    SELECT\n' +
        ',\n'.join(expr for expr in column_expressions) +
        '\n' +
        '    FROM trend."{}" AS t\n'.format(part_data.name) +
        '    WHERE $1 < timestamp AND timestamp <= $2\n' +
        '    GROUP BY entity_id\n' +
        "$query$ USING $1 - interval '{}', $1;\n".format(target_granularity) +
        'END;\n'
    )

    return OrderedDict([
        ('return_type', SqlSrc(return_type)),
        ('src', SqlSrc(src)),
        ('language', 'plpgsql')
    ])


def define_materialization_sql(target_name):
    return [
        'SELECT trend_directory.define_function_materialization(\n',
        "    id, '30m'::interval, '5m'::interval, '3 days'::interval, 'trend.{}(timestamp with time zone)'::regprocedure\n".format(target_name),
        ')\n',
        'FROM trend_directory.trend_store_part\n',
        "WHERE name = '{}';\n".format(target_name),
    ]


def define_trend_store_link(part_name, mapping_function, target_name):
    return [
        'INSERT INTO trend_directory.materialization_trend_store_link(materialization_id, trend_store_part_id, timestamp_mapping_func)\n',
        "SELECT m.id, tsp.id, 'trend.{}(timestamp with time zone)'::regprocedure\n".format(mapping_function),
        'FROM trend_directory.materialization m, trend_directory.trend_store_part tsp\n',
        "WHERE m::text = '{}' and tsp.name = '{}';\n".format(
            target_name, part_name
        )
    ]


def fingerprint_function_sql(
        part_data: TrendStorePart, source_granularity: str, target_granularity: str) -> str:
    return (
        "SELECT max(modified.last), format('{%s}', string_agg(format('\"%s\":\"%s\"', t, modified.last), ','))::jsonb\n"
        f"FROM generate_series($1 - interval '{target_granularity}' + interval '{source_granularity}', $1, interval '{source_granularity}') t\n"
        'LEFT JOIN (\n'
        '  SELECT timestamp, last\n'
        '  FROM trend_directory.trend_store_part part\n'
        '  JOIN trend_directory.modified ON modified.trend_store_part_id = part.id\n'
        f'  WHERE part.name = \'{part_data.name}\'\n'
        ') modified ON modified.timestamp = t;\n'
    )