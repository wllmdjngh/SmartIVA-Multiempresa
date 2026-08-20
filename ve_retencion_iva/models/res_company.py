from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    ve_cuenta_iva_retenido_cobrar_id = fields.Many2one(
        'account.account',
        string='IVA Retenido por Cobrar',
        domain="[('company_ids', 'in', id)]",
        help='Cuenta deudora que se debita al confirmar una retención IVA recibida (ve_retencion_iva).',
    )
    ve_cuenta_iva_por_pagar_id = fields.Many2one(
        'account.account',
        string='IVA por Pagar',
        domain="[('company_ids', 'in', id)]",
        help='Cuenta acreedora que se acredita al confirmar una retención IVA (ve_retencion_iva).',
    )
    ve_rif_cliente = fields.Char(
        string='RIF del Cliente AET',
        help='RIF (real o simbólico) del "Cliente" en AutomationEdge — la BD '
             'que agrupa las credenciales SENIAT de esta y otras "Empresas" '
             '(normalmente el RIF del despacho contable que gestiona esta '
             'compañía). Vacío = esta compañía es su propio Cliente en AET. '
             'A propósito NO usa res.company.parent_id (Sucursales): esa '
             'jerarquía nativa es para una misma entidad legal con varias '
             'sucursales, no para un despacho contable con clientes '
             'externos de RIF propio — además Odoo bloquea cambiarla una '
             'vez que la compañía tiene contabilidad posteada.',
    )

    ve_declarado_manual = fields.Boolean(
        string='Declarado SENIAT: carga manual',
        help='Activado: el monto "Declarado" del Dashboard Gerencial (gráfico '
             'Pendiente/Conciliado/Declarado/SENIAT, año en curso) sale de lo '
             'que el contador carga a mano mes a mes (ve.declarado.mensual) -- '
             'el número real que el cliente declaró ante el SENIAT. '
             'Desactivado (default): se calcula desde Odoo, sumando las '
             'retenciones con Estado Declaración = Declarado por mes.',
    )

    def ve_rif_cliente_aet(self):
        """RIF (o identificador simbólico) de la BD de AutomationEdge — el
        "Cliente" en el vocabulario de AET, que agrupa una o varias
        "Empresas" (cada una con su propio RIF real y su propia clave
        SENIAT dentro de esa BD). Si `ve_rif_cliente` está vacío, esta
        compañía es su propio Cliente. Definido acá (no inline en cada
        llamada al RPA) porque lo usan al menos 2 disparadores distintos
        (Extraer SENIAT y Declarar) — 2026-07-26, ver REQUISITOS.md
        MULTI-04/MULTI-07. Revisado el mismo día: se cambió de
        `parent_id.vat` a este campo propio (ver `ve_rif_cliente` arriba)."""
        self.ensure_one()
        return self.ve_rif_cliente or self.vat or False
