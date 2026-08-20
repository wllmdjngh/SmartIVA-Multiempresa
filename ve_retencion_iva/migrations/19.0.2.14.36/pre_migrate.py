import logging

_logger = logging.getLogger(__name__)

# Campo nuevo ve.conciliacion.periodo.monto_conciliado -- ver models/ve_conciliacion.py.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS monto_conciliado numeric DEFAULT 0
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.36: columna monto_conciliado asegurada en ve_conciliacion_periodo')
