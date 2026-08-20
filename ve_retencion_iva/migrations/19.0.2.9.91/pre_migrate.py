import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Las cuentas "IVA Retenido por Cobrar" / "IVA por Pagar" (usadas por
# ve.wh.iva._crear_asiento_contable) eran un solo par de ir.config_parameter
# GLOBAL para toda la base — con varias compañías (piloto/despacho contable),
# cada una necesita su propia cuenta (no pueden compartir account_id entre
# compañías, action_confirmar fallaría por company_id no coincidente).
# Ahora viven en res.company (ve_cuenta_iva_retenido_cobrar_id /
# ve_cuenta_iva_por_pagar_id) — este backfill respeta a qué compañía(s)
# pertenece cada cuenta (account.account.company_ids), no las asigna a
# ciegas a todas las compañías existentes.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE res_company
        ADD COLUMN IF NOT EXISTS ve_cuenta_iva_retenido_cobrar_id integer;
    """)
    cr.execute("""
        ALTER TABLE res_company
        ADD COLUMN IF NOT EXISTS ve_cuenta_iva_por_pagar_id integer;
    """)

    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env['ir.config_parameter'].sudo()
    cobrar_id = icp.get_param('ve_retencion_iva.cuenta_iva_retenido_cobrar_id')
    pagar_id = icp.get_param('ve_retencion_iva.cuenta_iva_por_pagar_id')

    n_cobrar = n_pagar = 0
    if cobrar_id:
        cta_cobrar = env['account.account'].sudo().browse(int(cobrar_id))
        if cta_cobrar.exists():
            for company in cta_cobrar.company_ids:
                if not company.ve_cuenta_iva_retenido_cobrar_id:
                    company.ve_cuenta_iva_retenido_cobrar_id = cta_cobrar.id
                    n_cobrar += 1

    if pagar_id:
        cta_pagar = env['account.account'].sudo().browse(int(pagar_id))
        if cta_pagar.exists():
            for company in cta_pagar.company_ids:
                if not company.ve_cuenta_iva_por_pagar_id:
                    company.ve_cuenta_iva_por_pagar_id = cta_pagar.id
                    n_pagar += 1

    _logger.info(
        've_retencion_iva 19.0.2.9.91: cuentas IVA Retenido por Cobrar/Por '
        'Pagar migradas de ir.config_parameter global a res.company '
        '(%d compañía(s) cobrar, %d compañía(s) pagar)', n_cobrar, n_pagar)
