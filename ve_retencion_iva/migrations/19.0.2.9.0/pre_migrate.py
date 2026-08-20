import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Agregar columna declaracion_iva_id a ve_conciliacion_periodo
    # antes de que el ORM procese el modelo (para que la FK ya exista)
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS declaracion_iva_id INTEGER
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva_prov
        ADD COLUMN IF NOT EXISTS declaracion_iva_id INTEGER
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.0: columnas declaracion_iva_id agregadas')
