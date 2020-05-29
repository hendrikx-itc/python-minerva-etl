import os
from contextlib import closing
import sys
import glob
from pathlib import Path

import yaml
from minerva.commands.live_monitor import live_monitor

from minerva.db import connect

from minerva.instance import INSTANCE_ROOT_VARIABLE
from minerva.commands.attribute_store import create_attribute_store_from_json, \
    DuplicateAttributeStore, SampledViewMaterialization
from minerva.commands.trend_store import create_trend_store_from_json, \
    DuplicateTrendStore
from minerva.commands.notification_store import \
    create_notification_store_from_json, DuplicateNotificationStore
from minerva.commands.partition import create_partitions_for_trend_store
from minerva.commands.trigger import create_trigger_from_config
from minerva.commands.load_sample_data import load_sample_data
from minerva.commands.relation import DuplicateRelation, define_relation, \
    materialize_relations
from minerva.commands.virtual_entity import materialize_virtual_entities
from minerva.commands.trend_materialization import define_materialization


def setup_command_parser(subparsers):
    cmd = subparsers.add_parser(
        'initialize',
        help='command for complete initialization of Minerva instance'
    )

    cmd.add_argument(
        '-i', '--instance-root',
        help='root directory of the instance definition'
    )

    cmd.add_argument(
        '--load-sample-data', action='store_true', default=False,
        help='generate and load sample data as specified in instance'
    )

    cmd.add_argument(
        '--live', action='store_true', default=False,
        help='live monitoring for materializations after initialization'
    )

    cmd.add_argument(
        '--num-partitions',
        help='number of partitions to create (default is full retention period)'
    )

    cmd.set_defaults(cmd=initialize_cmd)


def initialize_cmd(args):
    instance_root = (
        args.instance_root or os.environ.get(INSTANCE_ROOT_VARIABLE) or os.getcwd()
    )

    sys.stdout.write(
        "Initializing Minerva instance from '{}'\n".format(
            instance_root
        )
    )

    try:
        initialize_instance(instance_root, args.num_partitions)
    except Exception as exc:
        sys.stdout.write("Error:\n{}".format(str(exc)))
        raise exc

    if args.load_sample_data:
        header('Loading sample data')
        load_sample_data(instance_root)

    initialize_derivatives(instance_root)

    if args.live:
        header('Live monitoring for materializations')

        try:
            live_monitor()
        except KeyboardInterrupt:
            print("Stopped")


def header(title):
    width = (len(title) + 4)

    print('')
    print('#' * width)
    print('# {} #'.format(title))
    print('#' * width)
    print('')


def initialize_instance(instance_root, num_partitions):
    header('Custom pre-init SQL')
    load_custom_pre_init_sql(instance_root)

    header("Initializing attribute stores")
    initialize_attribute_stores(instance_root)

    header("Initializing trend stores")
    initialize_trend_stores(instance_root)

    header("Initializing notification stores")
    initialize_notification_stores(instance_root)

    header("Initializing virtual entities")
    define_virtual_entities(instance_root)

    header("Defining relations")
    define_relations(instance_root)

    header('Initializing trend materializations')
    define_trend_materializations(instance_root)

    header('Initializing attribute materializations')
    define_attribute_materializations(instance_root)

    header('Initializing triggers')
    define_triggers(instance_root)

    header('Creating partitions')
    create_partitions(num_partitions)

    header('Custom post-init SQL')
    load_custom_post_init_sql(instance_root)


def initialize_derivatives(instance_root):
    header('Materializing virtual entities')
    materialize_virtual_entities()

    header('Materializing relations')
    materialize_relations()


def initialize_attribute_stores(instance_root):
    definition_files = glob.glob(
        os.path.join(instance_root, 'attribute/*.yaml')
    )

    for definition_file_path in definition_files:
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

        try:
            create_attribute_store_from_json(definition)
        except DuplicateAttributeStore as exc:
            print(exc)

    # Attribute-store-like views can be used for quick attribute
    # transformations or combinations. These views can be defined using plain
    # SQL.
    sql_files = glob.glob(
        os.path.join(instance_root, 'attribute/*.sql')
    )

    for sql_file_path in sql_files:
        print(sql_file_path)

        execute_sql_file(sql_file_path)


def initialize_trend_stores(instance_root):
    definition_files = Path(instance_root, 'trend').rglob('*.yaml')

    for definition_file_path in sorted(definition_files):
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

        try:
            create_trend_store_from_json(definition)
        except DuplicateTrendStore as exc:
            print(exc)


def initialize_notification_stores(instance_root):
    definition_files = Path(instance_root, 'notification').rglob('*.yaml')

    for definition_file_path in definition_files:
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

        try:
            create_notification_store_from_json(definition)
        except DuplicateNotificationStore as exc:
            print(exc)


def define_virtual_entities(instance_root):
    definition_files = glob.glob(
        os.path.join(instance_root, 'virtual-entity/*.sql')
    )

    for definition_file_path in definition_files:
        print(definition_file_path)

        execute_sql_file(definition_file_path)


def define_relations(instance_root):
    definition_files = glob.glob(os.path.join(instance_root, 'relation/*.yaml'))

    for definition_file_path in definition_files:
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

        try:
            define_relation(definition)
        except DuplicateRelation as exc:
            print(exc)


def execute_sql_file(file_path):
    with open(file_path) as definition_file:
        sql = definition_file.read()

    with closing(connect()) as conn:
        conn.autocommit = True

        with closing(conn.cursor()) as cursor:
            cursor.execute(sql)


def define_trend_materializations(instance_root):
    # Load YAML based materializations
    yaml_definition_files = glob.glob(
        os.path.join(instance_root, 'materialization/*.yaml')
    )

    for definition_file_path in yaml_definition_files:
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

        define_materialization(definition)


def load_custom_pre_init_sql(instance_root):
    glob_pattern = os.path.join(
        instance_root, 'custom/pre-init/**/*.sql'
    )

    definition_files = glob.glob(glob_pattern, recursive=True)

    for definition_file_path in sorted(definition_files):
        print(definition_file_path)

        execute_sql_file(definition_file_path)


def load_custom_post_init_sql(instance_root):
    glob_pattern = os.path.join(
        instance_root, 'custom/post-init/**/*.sql'
    )

    definition_files = glob.glob(glob_pattern, recursive=True)

    for definition_file_path in sorted(definition_files):
        print(definition_file_path)

        execute_sql_file(definition_file_path)


def define_triggers(instance_root):
    definition_files = glob.glob(os.path.join(instance_root, 'trigger/*.yaml'))

    for definition_file_path in definition_files:
        print(definition_file_path)

        with open(definition_file_path) as definition_file:
            definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

            create_trigger_from_config(definition)


def create_partitions(num_partitions):
    partitions_created = 0
    query = "SELECT id FROM trend_directory.trend_store"

    with closing(connect()) as conn:
        conn.autocommit = True

        with closing(conn.cursor()) as cursor:
            cursor.execute(query)

            rows = cursor.fetchall()

        for trend_store_id, in rows:
            for name, partition_index, i, num in create_partitions_for_trend_store(conn, trend_store_id, '1 day', num_partitions):
                print(' ' * 60, end='\r')
                print('{} - {} ({}/{})'.format(name, partition_index, i, num), end="\r")
                partitions_created += 1

    print(" " * 60, end='\r')
    print('Created {} partitions'.format(partitions_created))


def define_attribute_materializations(instance_root):
    definition_files = glob.glob(
        os.path.join(instance_root, 'attribute/materialization/*.yaml')
    )

    with closing(connect()) as conn:
        conn.autocommit = True

        for definition_file_path in definition_files:
            print(definition_file_path)

            with open(definition_file_path) as definition_file:
                definition = yaml.load(definition_file, Loader=yaml.SafeLoader)

                materialization = SampledViewMaterialization.from_json(
                    definition
                )

                print(materialization)

                materialization.create(conn)