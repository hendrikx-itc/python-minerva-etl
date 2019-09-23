from minerva.db.query import Table

name = "trend_directory"

modified = Table(name, "modified")
trend_store = Table(name, "trend_store")
table_trend_store = Table(name, "table_trend_store")
view_trend_store = Table(name, "view_trend_store")


system_columns = ['entity_id', 'timestamp', 'created', 'job_id']
system_columns_set = set(system_columns)
