import logging

_logger = logging.getLogger(__name__)

# Campo nuevo ve.conciliacion.periodo.rifs_seniat_no_spe -- ver models/ve_conciliacion.py.
# Pedido explícito 2026-08-18: la carga de Retenciones SENIAT (XLSX y RPA)
# ya no marca sola es_agente_retencion en el cliente cuando un RIF trae
# retención SENIAT pero no está marcado como Contribuyente Especial -- en
# vez de eso reporta el caso acá para revisión manual.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS rifs_seniat_no_spe text
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.99: columna rifs_seniat_no_spe asegurada '
        'en ve_conciliacion_periodo')
