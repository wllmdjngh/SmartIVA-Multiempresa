import logging

_logger = logging.getLogger(__name__)

# Nuevo campo declarado_sin_comprobante (Boolean) en ve.wh.iva — columna
# simple, default False, no requiere backfill de datos (registros
# existentes seguirán en False, que es el comportamiento anterior).


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS declarado_sin_comprobante boolean DEFAULT false;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.10.1: columna declarado_sin_comprobante '
        'agregada a ve_wh_iva')
