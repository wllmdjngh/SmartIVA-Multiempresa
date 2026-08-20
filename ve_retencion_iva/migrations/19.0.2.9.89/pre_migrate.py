import logging

_logger = logging.getLogger(__name__)

# Columna Compañía visible en las listas: ve.seniat.retencion no tenía
# company_id propio (usaba conciliacion_id.company_id sin campo real).
# Ahora related+store igual que ve.declaracion.iva (19.0.2.9.87) — mismo
# backfill vía join con ve_conciliacion_periodo.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_seniat_retencion
        ADD COLUMN IF NOT EXISTS company_id integer;
    """)
    cr.execute("""
        UPDATE ve_seniat_retencion s
        SET company_id = p.company_id
        FROM ve_conciliacion_periodo p
        WHERE s.conciliacion_id = p.id AND s.company_id IS NULL;
    """)

    _logger.info(
        've_retencion_iva 19.0.2.9.89: company_id agregado y respaldado en '
        've_seniat_retencion (columna Compañía en la lista)')
