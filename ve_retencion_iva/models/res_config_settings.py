from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Antes eran ir.config_parameter GLOBALES (una sola cuenta para toda la
    # base de datos) — con varias compañías (piloto/despacho contable), cada
    # una necesita su PROPIA cuenta (no pueden compartir account_id entre
    # compañías). Ahora viven en res.company (ve_cuenta_iva_*) y esto es
    # solo el related estándar de Ajustes, scoped a la compañía que se
    # esté configurando.
    cuenta_iva_retenido_cobrar_id = fields.Many2one(
        'account.account',
        string='IVA Retenido por Cobrar',
        related='company_id.ve_cuenta_iva_retenido_cobrar_id',
        readonly=False,
        help='Cuenta deudora que se debita al confirmar una retención IVA recibida.',
    )
    cuenta_iva_por_pagar_id = fields.Many2one(
        'account.account',
        string='IVA por Pagar',
        related='company_id.ve_cuenta_iva_por_pagar_id',
        readonly=False,
        help='Cuenta acreedora que se acredita al confirmar una retención IVA.',
    )
    ve_rif_cliente = fields.Char(
        string='RIF del Cliente AET',
        related='company_id.ve_rif_cliente',
        readonly=False,
        help='RIF (real o simbólico) del "Cliente" en AutomationEdge — normalmente '
             'el RIF del despacho contable que gestiona esta compañía. Vacío = '
             'esta compañía es su propio Cliente en AET (usa su propio RIF).',
    )

    ve_declarado_manual = fields.Boolean(
        string='Declarado SENIAT: carga manual',
        related='company_id.ve_declarado_manual',
        readonly=False,
        help='Activado: el monto "Declarado" del Dashboard Gerencial sale de la '
             'carga manual mensual (menú Utilitarios → Cargar Declarado SENIAT). '
             'Desactivado: se calcula desde Odoo (retenciones con Estado '
             'Declaración = Declarado, por mes).',
    )

    # ── Parámetros Estimador de Riesgo / Sanciones ────────────────────────────
    ve_es_agente_retencion = fields.Boolean(
        string='Empresa es Agente de Retención SPE',
        help='Determina la prescripción de sanciones: 6 años (agente SPE) o 10 años (contribuyente). '
             'Usado por el Estimador de Riesgo SENIAT.',
        config_parameter='ve_retencion_iva.es_agente_retencion',
    )
    ve_sancion_por_comprobante_bs = fields.Float(
        string='Sanción por Comprobante No Entregado (Bs)',
        digits=(16, 2),
        help='Monto estimado de la multa COT Art. 101 por cada comprobante no entregado en plazo. '
             'Consultar con el contador el valor vigente.',
        config_parameter='ve_retencion_iva.sancion_por_comprobante_bs',
    )
    ve_porcentaje_omision = fields.Float(
        string='% Sanción por Omisión de Declaración (Art. 111 COT)',
        digits=(5, 2),
        help='Porcentaje del débito fiscal aplicado como sanción por omisión de declaración. '
             'Rango legal: 10%–25%. Default: 25% (peor caso).',
        config_parameter='ve_retencion_iva.porcentaje_omision',
    )
