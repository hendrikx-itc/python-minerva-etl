from minerva.storage.trend.trendstore import TrendStore
from minerva.directory import DataSource, EntityType
from minerva.storage.trend.granularity import create_granularity


class ViewTrendStoreDescriptor():
    def __init__(
            self, data_source, entity_type, granularity, query):
        self.data_source = data_source
        self.entity_type = entity_type
        self.granularity = granularity
        self.query = query


class TrendStoreQuery():
    def __init__(self, trend_store, trend_names):
        self.trend_store = trend_store
        self.trend_names = trend_names

    def execute(self, cursor):
        cursor.execute('SELECT 1')

        return cursor.fetchall()


class ViewTrendStore(TrendStore):
    @staticmethod
    def create(descriptor):
        def f(cursor):
            args = (
                descriptor.data_source.name,
                descriptor.entity_type.name,
                str(descriptor.granularity),
                descriptor.query
            )

            query = (
                "SELECT * FROM trend_directory.create_view_trend_store("
                "%s, %s, %s, %s"
                ")"
            )

            cursor.execute(query, args)

            (
                trend_store_id, entity_type_id, data_source_id, granularity_str
            ) = cursor.fetchone()

            entity_type = EntityType.get(entity_type_id)(cursor)
            data_source = DataSource.get(data_source_id)(cursor)

            trends = ViewTrendStore.get_trends(cursor, trend_store_id)

            return ViewTrendStore(
                trend_store_id, data_source, entity_type,
                create_granularity(granularity_str), trends
            )

        return f

    def retrieve(self, trend_names):
        return TrendStoreQuery(self, trend_names)