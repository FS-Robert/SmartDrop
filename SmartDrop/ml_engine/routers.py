"""Database router: enruta los modelos de ml_engine a un alias de DB propio.

Por defecto ese alias apunta al mismo SQLite que el resto del proyecto (para
poder generar datos sintéticos y entrenar localmente sin infraestructura
adicional, según lo solicitado). Cuando se configuren las variables de
entorno ML_TIMESERIES_DB_* (ver settings.py), ese alias apuntará a Postgres
(Supabase) y se podrá usar pg_partman (ver management/commands/ml_setup_partitioning.py)
sin tocar una sola línea de este paquete.
"""
DB_ALIAS = 'timeseries'


class MlEngineRouter:
    app_label = 'ml_engine'

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return DB_ALIAS
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return DB_ALIAS
        return None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        if self.app_label in labels:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == DB_ALIAS
        # No migrar nada más en el alias 'timeseries' (evita duplicar auth/sessions ahí).
        if db == DB_ALIAS:
            return False
        return None
