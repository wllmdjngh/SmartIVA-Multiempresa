from . import models, controllers, wizards


def post_init_hook(env):
    """Configura cuentas contables por defecto al instalar el módulo, por
    cada compañía existente (cada una con su propio plan de cuentas)."""
    for company in env['res.company'].sudo().search([]):
        _set_default_account(env, company, 've_cuenta_iva_retenido_cobrar_id', '1151004')
        _set_default_account(env, company, 've_cuenta_iva_por_pagar_id', '2172003')


def _set_default_account(env, company, field_name, code):
    if company[field_name]:
        return  # ya configurado, no sobreescribir
    account = env['account.account'].sudo().search(
        [('code', '=', code), ('company_ids', 'in', company.id)], limit=1)
    if account:
        company.sudo()[field_name] = account.id
