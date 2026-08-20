import logging

_logger = logging.getLogger(__name__)

# Campo nuevo ve.wh.iva.diferencia_archivo_aceptada -- ver models/ve_wh_iva.py.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS diferencia_archivo_aceptada boolean DEFAULT false
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.22: columna diferencia_archivo_aceptada asegurada en ve_wh_iva')
