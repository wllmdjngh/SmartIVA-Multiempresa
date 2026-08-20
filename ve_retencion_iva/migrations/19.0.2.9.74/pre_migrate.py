import logging

_logger = logging.getLogger(__name__)

# Registro de llamada telefónica desde la Lista de Trabajo: agrega la
# columna fecha_ultima_llamada a ve_wh_iva.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva
        ADD COLUMN IF NOT EXISTS fecha_ultima_llamada timestamp;
    """)
    _logger.info(
        've_retencion_iva 19.0.2.9.74: columna fecha_ultima_llamada agregada a ve_wh_iva')
