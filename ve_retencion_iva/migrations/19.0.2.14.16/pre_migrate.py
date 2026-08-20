import logging

_logger = logging.getLogger(__name__)

# pct_diferencia_archivo pasa de Float a Char (ver ve_wh_iva.py -- necesita
# poder mostrar "Archivo=0" en vez de un 0% engañoso cuando el archivo
# traía 0 y Odoo calculó un monto). DROP + ADD en vez de dejar que el ORM
# intente convertir el tipo solo -- no hace falta preservar el valor
# numérico viejo, se recalcula en post_migrate.
#
# es_agente_retencion (related+store, Contribuyente Especial) es campo
# nuevo, columna asegurada por las dudas.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva DROP COLUMN IF EXISTS pct_diferencia_archivo
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva ADD COLUMN IF NOT EXISTS pct_diferencia_archivo varchar
    """)
    cr.execute("""
        ALTER TABLE ve_wh_iva ADD COLUMN IF NOT EXISTS es_agente_retencion boolean
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.16: pct_diferencia_archivo recreada como '
        'varchar, es_agente_retencion asegurada')
