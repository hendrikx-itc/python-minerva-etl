# -*- coding: utf-8 -*-
__docformat__ = "restructuredtext en"

__copyright__ = """
Copyright (C) 2008-2013 Hendrikx-ITC B.V.

Distributed under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3, or (at your option) any later
version.  The full license is in the file COPYING, distributed as part of
this software.
"""
from contextlib import closing
import psycopg2.errorcodes

from minerva.directory.basetypes import Tag, TagGroup

from minerva.db.util import create_temp_table, drop_table, \
    create_copy_from_file

SCHEMA = "directory"


class NoSuchTagError(Exception):
    """
    Exception raised when no matching Tag is found.
    """
    pass


class NoSuchTagGroupError(Exception):
    """
    Exception raised when no matching TagGroup is found.
    """
    pass


def tag_entities(conn, tag_links):
    """
    Tag entities by updating directory.entitytaglink table

    :param conn: database connection
    :param tag_links: list of tuples like (entity_id, tag_name)
    """
    group_id = get_taggroup_id(conn, 'default')

    tag_links_with_group_id = [
        (entity_id, tag_name, group_id)
        for entity_id, tag_name in tag_links
    ]

    store_in_staging_table(conn, tag_links_with_group_id)

    with closing(conn.cursor()) as cursor:
        cursor.execute("SELECT entity_tag.process_staged_links();")

    conn.commit()


def get_taggroup_id(conn, name):
    with closing(conn.cursor()) as cursor:
        cursor.execute(
            "SELECT id FROM directory.taggroup WHERE name = %s",
            (name,)
        )

        group_id, = cursor.fetchone()

    return group_id


def store_in_staging_table(conn, tag_links):
    """
    Create temporay table with tag links

    :param conn: Minerva database connection
    :param tag_links: list of tuples like (trend_id, tag_name, taggroup_id)
    """
    table_name = "entity_tag.entitytaglink_staging"
    column_names = ["entity_id", "tag_name", "taggroup_id"]

    copy_from_file = create_copy_from_file(tag_links, ('d', 's', 'd'))

    with closing(conn.cursor()) as cursor:
        cursor.copy_from(copy_from_file, table_name, columns=column_names)


def flush_tag_links(conn, tag_name):
    query = (
        "DELETE FROM {0}.entitytaglink etl "
        "USING {0}.tag tag "
        "WHERE tag.id = etl.tag_id AND tag.name = %s").format(SCHEMA)

    args = (tag_name, )

    with closing(conn.cursor()) as cursor:
        cursor.execute(query, args)


def create_tag_group(conn, name, complementary):
    """
    Create and return tag group
    :param conn: psycopg2 connection
    :param name: name of tag group
    :param properties: dictionary containing properties like 'complementary'
    etc
    """
    insert_query = (
        "INSERT INTO directory.taggroup (id, name, complementary) "
        "VALUES (DEFAULT, %s, %s) "
        "RETURNING id ")

    with closing(conn.cursor()) as cursor:

            try:
                cursor.execute(insert_query, (name, complementary))
            except psycopg2.Error as exc:
                conn.rollback()

                if exc.pgcode == psycopg2.errorcodes.UNIQUE_VIOLATION:
                    return get_tag_group(conn, name)
                else:
                    raise exc
            else:
                (id,) = cursor.fetchone()

                conn.commit()

    return TagGroup(id, name, complementary)


def create_tag(conn, name, group, description=""):
    """
    Create new and return new tag
    :param conn: A psycopg2 connection.
    :param name: Name of tag.
    :param group: TagGroup object, specifying group of tag. If None,
    'default' tag group is used
    :param description: Description (optional)
    """
    insert_query = (
        "INSERT INTO directory.tag (id, name, taggroup_id, description) "
        "VALUES (DEFAULT, %s, %s, %s) "
        "RETURNING id")

    with closing(conn.cursor()) as cursor:
        try:
            cursor.execute(insert_query, (name, group.id, description))
        except psycopg2.Error as exc:
            conn.rollback()

            if exc.pgcode == psycopg2.errorcodes.UNIQUE_VIOLATION:
                return get_tag(conn, name)
            else:
                raise exc
        else:
            (id,) = cursor.fetchone()

            conn.commit()

    return Tag(id, name, description)


def get_tag(conn, name):
    """
    Return tag with specified name.
    :param conn: psycopg2 database connection
    :param name: name of tag
    """
    query = (
        "SELECT id, name, taggroup_id, description "
        "FROM directory.tag WHERE lower(name) = lower(%s)")

    with closing(conn.cursor()) as cursor:
        cursor.execute(query, (name,))

        if cursor.rowcount == 1:
            id, name, group_id, description = cursor.fetchone()

            return Tag(id, name, group_id, description)
        else:
            raise NoSuchTagError("No tag with name {0}".format(name))


def get_tag_group(conn, name):
    """
    Return tag group with specified name.
    :param conn: psycopg2 database connection
    :param name: name of tag group
    """
    query = (
        "SELECT id, name, complementary "
        "FROM directory.taggroup WHERE lower(name) = lower(%s)")

    with closing(conn.cursor()) as cursor:
        cursor.execute(query, (name,))

        if cursor.rowcount == 1:
            id, name, complementary = cursor.fetchone()

            return TagGroup(id, name, complementary)
        else:
            raise NoSuchTagGroupError(
                "No tag group with name {0}".format(name))


def get_tags_for_entity_id(conn, entity_id):
    """
    Return tags for specific entity.
    """
    query = (
        "SELECT tag.id, tag.name, tag.taggroup_id, tag.description "
        "FROM directory.entitytaglink etl "
        "JOIN directory.tag tag on tag.id = etl.tag_id "
        "WHERE etl.entity_id = %s")

    args = (entity_id, )

    with closing(conn.cursor()) as cursor:
        cursor.execute(query, args)

        if cursor.rowcount > 0:
            return [Tag(id, name, group_id, description)
                    for id, name, group_id, description in cursor.fetchall()]
        else:
            return []