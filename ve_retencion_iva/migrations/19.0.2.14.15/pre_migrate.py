import logging

_logger = logging.getLogger(__name__)

# Campo nuevo ve.wh.iva.viene_de_libro_ventas -- ver models/ve_wh_iva.py.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS viene_de_libro_ventas boolean DEFAULT false
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.15: columna viene_de_libro_ventas asegurada en ve_wh_iva')
