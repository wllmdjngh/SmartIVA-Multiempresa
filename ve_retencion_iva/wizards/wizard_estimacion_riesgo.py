from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class VeWizardEstimacionRiesgo(models.TransientModel):
    _name = 've.wizard.estimacion.riesgo'
    _description = 'Estimador de Riesgo SENIAT'

    # ── Parámetros ─────────────────────────────────────────────────────────────
    es_agente_retencion = fields.Boolean(
        string='Empresa es Agente de Retención SPE', default=True,
        help='Determina la ventana de prescripción: 6 años (agente SPE) o 10 años (contribuyente).')
    sancion_por_comprobante_bs = fields.Float(
        string='Sanción por Comprobante No Entregado (Bs)',
        digits=(16, 2),
        help='Monto estimado de la multa COT Art. 101 por cada comprobante no entregado en plazo.')
    porcentaje_omision = fields.Float(
        string='% Sanción Omisión Declaración (Art. 111 COT)',
        digits=(5, 2), default=25.0,
        help='Porcentaje del débito fiscal no declarado que se aplica como sanción.')
    tasa_eur_bcv = fields.Float(
        string='Tasa EUR/BCV (Bs por EUR)',
        digits=(16, 4),
        help='Tasa del BCV usada para convertir el riesgo estimado a EUR. '
             'Se llena automáticamente desde ve_bcv_rates.')

    # ── Resultados (generados por action_calcular) ─────────────────────────────
    line_ids = fields.One2many(
        've.wizard.estimacion.riesgo.line', 'wizard_id', string='Períodos en Riesgo')
    riesgo_total_bs = fields.Float(
        string='Riesgo Total (Bs)', digits=(16, 2),
        compute='_compute_totales')
    riesgo_total_eur = fields.Float(
        string='Riesgo Total (EUR)', digits=(16, 2),
        compute='_compute_totales')
    calculado = fields.Boolean(default=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        params = self.env['ir.config_parameter'].sudo()
        # Antes leía un ir.config_parameter GLOBAL (una sola respuesta para
        # toda la base) — si es agente de retención SPE es un hecho propio
        # de CADA compañía (ya existe como campo real en su contacto,
        # company.partner_id.es_agente_retencion, usado por account_move.py
        # para la regla "no retención entre SPE"). Con varias compañías
        # (piloto/despacho contable) el valor global podía no aplicar a la
        # compañía activa. El usuario igual puede corregirlo a mano en el
        # wizard si no aplica.
        res['es_agente_retencion'] = bool(self.env.company.partner_id.es_agente_retencion)
        sancion_bs = params.get_param('ve_retencion_iva.sancion_por_comprobante_bs', '0.0')
        res['sancion_por_comprobante_bs'] = float(sancion_bs or 0)
        pct = params.get_param('ve_retencion_iva.porcentaje_omision', '25.0')
        res['porcentaje_omision'] = float(pct or 25.0)
        rate = self.env['res.currency.rate'].search([
            ('currency_id.name', '=', 'EUR'),
            ('company_id', '=', self.env.company.id),
        ], order='name desc', limit=1)
        if rate and rate.rate:
            res['tasa_eur_bcv'] = rate.rate
        return res

    @api.depends('line_ids.total_bs', 'line_ids.total_eur')
    def _compute_totales(self):
        for rec in self:
            rec.riesgo_total_bs = sum(rec.line_ids.mapped('total_bs'))
            rec.riesgo_total_eur = sum(rec.line_ids.mapped('total_eur'))

    def action_calcular(self):
        self.ensure_one()
        self.line_ids.unlink()
        years = 6 if self.es_agente_retencion else 10
        fecha_corte = fields.Date.today() - relativedelta(years=years)
        periodos = self.env['ve.conciliacion.periodo'].search([
            ('fecha_fin', '>=', fecha_corte),
            ('estado', '!=', 'declarado'),
        ], order='fecha_fin asc')
        WH = self.env['ve.wh.iva']
        lines = []
        for p in periodos:
            n_vencidos = WH.search_count([('conciliacion_id', '=', p.id), ('state', '=', 'vencido')])
            n_esperados = WH.search_count([('conciliacion_id', '=', p.id), ('state', '=', 'esperado')])
            n_riesgo = n_vencidos + n_esperados
            san_ilicito = n_riesgo * self.sancion_por_comprobante_bs
            # Omisión solo si el período ni siquiera está aprobado
            san_omision = 0.0
            campo_49 = p.declaracion_iva_id.campo_49 if p.declaracion_iva_id else 0.0
            if p.estado not in ('aprobado', 'declarado') and campo_49 > 0:
                san_omision = campo_49 * self.porcentaje_omision / 100.0
            total_bs = san_ilicito + san_omision
            total_eur = (total_bs / self.tasa_eur_bcv) if self.tasa_eur_bcv > 0 else 0.0
            if n_riesgo > 0 or total_bs > 0:
                lines.append({
                    'wizard_id': self.id,
                    'periodo_id': p.id,
                    'periodo_name': p.periodo_retencion or p.name or str(p.id),
                    'n_vencidos': n_vencidos,
                    'n_esperados': n_esperados,
                    'sancion_ilicito_bs': san_ilicito,
                    'sancion_omision_bs': san_omision,
                    'total_bs': total_bs,
                    'total_eur': total_eur,
                })
        if lines:
            self.env['ve.wizard.estimacion.riesgo.line'].create(lines)
        self.calculado = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': 've.wizard.estimacion.riesgo',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_crear_sancion(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError('Primero ejecuta el cálculo (botón "Calcular Riesgo").')
        line_vals = []
        for ln in self.line_ids:
            if ln.sancion_ilicito_bs > 0:
                line_vals.append((0, 0, {
                    'periodo_id': ln.periodo_id.id if ln.periodo_id else False,
                    'periodo_desc': ln.periodo_name,
                    'tipo': 'ilicito_formal',
                    'descripcion': f'{ln.n_vencidos + ln.n_esperados} comprobante(s) en riesgo',
                    'cantidad': ln.n_vencidos + ln.n_esperados,
                    'monto_bs': ln.sancion_ilicito_bs,
                    'tasa_eur_bcv': self.tasa_eur_bcv,
                }))
            if ln.sancion_omision_bs > 0:
                line_vals.append((0, 0, {
                    'periodo_id': ln.periodo_id.id if ln.periodo_id else False,
                    'periodo_desc': ln.periodo_name,
                    'tipo': 'omision',
                    'descripcion': f'Omisión estimada {self.porcentaje_omision}% débito fiscal',
                    'monto_bs': ln.sancion_omision_bs,
                    'tasa_eur_bcv': self.tasa_eur_bcv,
                }))
        sancion = self.env['ve.sancion.iva'].create({
            'name': f'Estimación Riesgo SENIAT {fields.Date.today().year}',
            'fecha': fields.Date.today(),
            'tipo_origen': 'auditoria_fiscal',
            'es_agente_retencion': self.es_agente_retencion,
            'estado': 'pendiente',
            'note': (f'Generado por Estimador de Riesgo SENIAT.\n'
                     f'Tasa EUR/BCV: {self.tasa_eur_bcv} Bs/EUR\n'
                     f'Parámetros: sanción/comp={self.sancion_por_comprobante_bs} Bs, '
                     f'omisión={self.porcentaje_omision}%'),
            'line_ids': line_vals,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sanción Creada',
            'res_model': 've.sancion.iva',
            'res_id': sancion.id,
            'view_mode': 'form',
            'target': 'current',
        }


class VeWizardEstimacionRiesgoLine(models.TransientModel):
    _name = 've.wizard.estimacion.riesgo.line'
    _description = 'Línea Estimación Riesgo SENIAT'

    wizard_id = fields.Many2one(
        've.wizard.estimacion.riesgo', required=True, ondelete='cascade')
    periodo_id = fields.Many2one('ve.conciliacion.periodo', string='Conciliación Período', readonly=True)
    periodo_name = fields.Char(string='Período', readonly=True)
    n_vencidos = fields.Integer(string='Vencidos', readonly=True)
    n_esperados = fields.Integer(string='Esperados', readonly=True)
    sancion_ilicito_bs = fields.Float(
        string='Ilícito Formal (Bs)', digits=(16, 2), readonly=True)
    sancion_omision_bs = fields.Float(
        string='Omisión (Bs)', digits=(16, 2), readonly=True)
    total_bs = fields.Float(string='Total (Bs)', digits=(16, 2), readonly=True)
    total_eur = fields.Float(string='Total (EUR)', digits=(16, 2), readonly=True)
