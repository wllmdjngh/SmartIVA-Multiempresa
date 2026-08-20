import logging

_logger = logging.getLogger(__name__)

# Campo nuevo res.company.ve_rif_cliente — reemplaza el uso de
# res.company.parent_id (Sucursales nativas) para modelar el "Cliente" de
# AutomationEdge, ver models/res_company.py y REQUISITOS.md MULTI-04/MULTI-07.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE res_company ADD COLUMN IF NOT EXISTS ve_rif_cliente varchar
    """)
    _logger.info('ve_retencion_iva 19.0.2.13.10: columna res_company.ve_rif_cliente asegurada')
