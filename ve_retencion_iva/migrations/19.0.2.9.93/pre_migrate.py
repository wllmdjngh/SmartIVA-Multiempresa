import logging

_logger = logging.getLogger(__name__)

# Las 3 secuencias de este módulo (ve.wh.iva, ve.wh.iva.prov,
# ve.conciliacion.periodo) quedaron scoped a la compañía que estaba activa
# cuando se instaló el módulo (típicamente la única/primera que existía) —
# ir.sequence pone company_id = env.company por defecto al crear si no se
# especifica lo contrario. Con una segunda compañía (piloto/cliente nuevo),
# next_by_code() ya no las encuentra desde esa otra compañía y el ref cae al
# fallback "RET-IVA-C/YYYY/????" en vez de numerar de verdad. Se quitan el
# company_id para que sirvan para cualquier compañía (mismo criterio que el
# data file cron_data.xml, que noupdate="1" no vuelve a aplicar solo).

_CODES = ('ve.wh.iva', 've.wh.iva.prov', 've.conciliacion.periodo')


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_sequence
        SET company_id = NULL
        WHERE code = ANY(%s) AND company_id IS NOT NULL
    """, (list(_CODES),))
    _logger.info(
        've_retencion_iva 19.0.2.9.93: %d secuencia(s) liberadas de su '
        'compañía (ve.wh.iva / ve.wh.iva.prov / ve.conciliacion.periodo)',
        cr.rowcount)
