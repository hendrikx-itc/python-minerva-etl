# -*- coding: utf-8 -*-
import sys
import logging
import json
import argparse
from contextlib import contextmanager
from contextlib import closing
from operator import itemgetter
import re

from minerva.storage.trend.tabletrendstore import NoSuchTableTrendStore
from minerva.util import compose, k
from minerva.directory import DataSource
import minerva.storage.trend.datapackage
import minerva.storage.attribute.datapackage
from minerva.directory.entitytype import NoSuchEntityType
from minerva.harvest.fileprocessor import process_file
from minerva.harvest.plugins import iter_entry_points, \
    get_plugin as get_harvest_plugin
from minerva.db import connect


class ConfigurationError(Exception):
    pass


package_name = "minerva_harvesting"


def setup_command_parser(subparsers):
    cmd = subparsers.add_parser(
        'load-data', help='command for loading data'
    )

    cmd.add_argument(
        "file_path", nargs="*",
        help="path of file that will be processed"
    )

    cmd.add_argument(
        "-p", "--plugin", action=_LoadHarvestPlugin,
        help="harvester plug-in to use for processing file(s)"
    )

    cmd.add_argument(
        "-l", "--list-plugins", action=_ListPlugins,
        help="list installed Harvester plug-ins")

    cmd.add_argument(
        "--parser-config", default="{}", type=json.loads,
        help="parser specific configuration"
    )

    cmd.add_argument(
        "--pretend", action="store_true", default=False,
        help="only process data, do not write to database"
    )

    cmd.add_argument(
        "--show-progress", action="store_true",
        dest="show_progress", default=False, help="show progressbar"
    )

    cmd.add_argument(
        "-v", "--verbose", action="store_true", dest="verbose",
        default=False, help="produce verbose output"
    )

    cmd.add_argument(
        "--debug", action="store_true", dest="debug",
        default=False, help="produce debug output"
    )

    cmd.add_argument(
        "--dn-filter", type=create_regex_filter, default=k(True),
        help="filter by distinguished name"
    )

    cmd.add_argument(
        "--column-filter", type=create_regex_filter,
        default=k(True), help="filter by trend name"
    )

    cmd.add_argument(
        "--data-source", default="load-data", help="data source to use"
    )

    cmd.add_argument(
        "--statistics", action="store_true", dest="statistics", default=False,
        help="show statistics like number of packages, entities, etc."
    )

    cmd.set_defaults(cmd=load_data_cmd(cmd))


def load_data_cmd(cmd_parser):
    def cmd(args):
        if args.debug:
            logging.root.setLevel(logging.DEBUG)

        if args.verbose:
            logging.root.addHandler(logging.StreamHandler())

        statistics = Statistics()

        if 'plugin' not in args:
            cmd_parser.print_help()
            return

        parser = args.plugin.create_parser(args.parser_config)

        if args.pretend:
            storage_provider = store_dummy
        else:
            storage_provider = create_store_db_context(
                args.data_source, parser.store_command()
            )

        try:
            with storage_provider() as store:
                handle_package = compose(
                    store,
                    tee(lambda package: print(package.render_table()))
                )

                for file_path in args.file_path:
                    if args.verbose:
                        logging.info("Processing {0}".format(file_path))

                    logging.info(
                        "Start processing file {0} using plugin {1}"
                        " and config {2}".format(
                            file_path, args.plugin, args.parser_config
                        )
                    )

                    for package in process_file(
                            file_path, parser, args.show_progress):
                        try:
                            handle_package(package)
                        except NoSuchEntityType as exc:
                            raise ConfigurationError(
                                'No such entity type \'{entity_type}\'\n'
                                'Create a data source using e.g.\n'
                                '\n'
                                '    minerva entity-type create {entity_type}\n'.format(
                                    entity_type=exc.entity_type_name
                                )
                            )
        except ConfigurationError as err:
            print('fatal: {}'.format(err))

        if args.statistics:
            for line in statistics.report():
                logging.info(line)

    return cmd


def create_regex_filter(x):
    if x:
        return re.compile(x).match
    else:
        return k(True)


class Statistics(object):
    def __init__(self):
        self.package_count = 0

    def extract_statistics(self, package):
        self.package_count += 1

        return package

    def report(self):
        return [
            "{} packages".format(self.package_count)
        ]


def filter_trend_package(entity_filter, trend_filter, package):
    filtered_trend_names = filter(trend_filter, package.trend_names)

    trend_filter_map = map(trend_filter, package.trend_names)

    filter_rows = compose(entity_filter, itemgetter(0))

    entity_filtered_rows = filter(filter_rows, package.rows)

    filtered_rows = []

    for row in entity_filtered_rows:
        values = row[-1]

        trend_filtered_values = [
            v
            for include, v in
            zip(trend_filter_map, values)
            if include
        ]

        trend_filtered_row = tuple(row[:-1]) + (trend_filtered_values,)

        filtered_rows.append(trend_filtered_row)

    return minerva.storage.trend.datapackage.DefaultPackage(
        package.granularity, package.timestamp, filtered_trend_names,
        filtered_rows
    )


def filter_attribute_package(entity_filter, attribute_filter, package):
    filtered_attribute_names = filter(attribute_filter, package.attribute_names)

    attribute_filter_map = map(attribute_filter, package.attribute_names)

    filter_rows = compose(entity_filter, itemgetter(0))

    entity_filtered_rows = filter(filter_rows, package.rows)

    filtered_rows = []

    for row in entity_filtered_rows:
        values = row[-1]

        attribute_filtered_values = [
            v
            for include, v in
            zip(attribute_filter_map, values)
            if include
        ]

        attribute_filtered_row = row[:-1] + (attribute_filtered_values,)

        filtered_rows.append(attribute_filtered_row)

    return minerva.storage.attribute.datapackage.DataPackage(
        filtered_attribute_names,
        filtered_rows)


package_data_filters = {
    "trend": filter_trend_package,
    "attribute": filter_attribute_package
}


def tee(fn):
    def wrapper(x):
        fn(x)

        return x

    return wrapper


class _ListPlugins(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super(_ListPlugins, self).__init__(
            option_strings=option_strings, dest=dest, default=default, nargs=0,
            help=help
        )

    def __call__(self, parser, namespace, values, option_string=None):
        for entry_point in iter_entry_points():
            print(entry_point.name)

        sys.exit(0)


class _LoadHarvestPlugin(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super(_LoadHarvestPlugin, self).__init__(
            option_strings=option_strings, dest=dest, default=default,
            nargs=1, help=help
        )

    def __call__(self, parser, namespace, values, option_string=None):
        plugin_name = values[0]

        plugin = get_harvest_plugin(plugin_name)

        if plugin is None:
            print("Data type '{0}' not supported".format(plugin_name))
            sys.exit(1)

        setattr(namespace, self.dest, plugin)


def create_store_db_context(data_source_name, store_cmd):
    @contextmanager
    def store_db_context():
        with closing(connect()) as conn:
            with closing(conn.cursor()) as cursor:
                data_source = DataSource.get_by_name(data_source_name)(cursor)

            if data_source is None:
                raise ConfigurationError(
                    'No such data source \'{data_source}\'\n'
                    'Create a data source using e.g.\n'
                    '\n'
                    '    minerva data-source create {data_source}\n'.format(
                        data_source=data_source_name
                    )
                )

            def store_package(package):
                try:
                    store_cmd(package)(data_source)(conn)
                except NoSuchTableTrendStore as exc:
                    raise ConfigurationError(
                        'No table trend store found for the combination ('
                        'data source: {data_source}, '
                        'entity type: {entity_type}, '
                        'granularity: {granularity}'
                        ')\n'
                        'Create a table trends store using e.g.\n'
                        '\n'
                        '    minerva trend-store create --from-json'.format(
                            data_source=exc.data_source.name,
                            entity_type=exc.entity_type.name,
                            granularity=exc.granularity
                        )
                    )

            yield store_package

    return store_db_context


@contextmanager
def store_dummy():
    yield no_op


def no_op(*args, **kwargs):
    pass
