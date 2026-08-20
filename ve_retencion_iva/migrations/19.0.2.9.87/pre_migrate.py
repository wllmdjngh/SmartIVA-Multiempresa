import logging

_logger = logging.getLogger(__name__)

# MULTI-02: company_id real en ve.conciliacion.periodo y ve.comprobante.inbox
# (antes ausentes) y en ve.declaracion.iva (antes derivado por heurística
# wh_iva_ids[:1].company_id, ahora related+store a conciliacion_id.company_id).
# Backfill a la única compañía existente — toda la data actual pertenece a
# una sola compañía en QA/PROD hoy.


def migrate(cr, version):
    cr.execute("SELECT id FROM res_company ORDER BY id LIMIT 1")
    row = cr.fetchone()
    default_company_id = row[0] if row else None

    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS company_id integer;
    """)
    if default_company_id:
        cr.execute("""
            UPDATE ve_conciliacion_periodo
            SET company_id = %s
            WHERE company_id IS NULL;
        """, (default_company_id,))

    cr.execute("""
        ALTER TABLE ve_comprobante_inbox
        ADD COLUMN IF NOT EXISTS company_id integer;
    """)
    if default_company_id:
        cr.execute("""
            UPDATE ve_comprobante_inbox
            SET company_id = %s
            WHERE company_id IS NULL;
        """, (default_company_id,))

    cr.execute("""
        ALTER TABLE ve_declaracion_iva
        ADD COLUMN IF NOT EXISTS company_id integer;
    """)
    cr.execute("""
        UPDATE ve_declaracion_iva d
        SET company_id = p.company_id
        FROM ve_conciliacion_periodo p
        WHERE d.conciliacion_id = p.id AND d.company_id IS NULL;
    """)

    _logger.info(
        've_retencion_iva 19.0.2.9.87: company_id agregado y respaldado en '
        've_conciliacion_periodo, ve_comprobante_inbox, ve_declaracion_iva (MULTI-02)')
