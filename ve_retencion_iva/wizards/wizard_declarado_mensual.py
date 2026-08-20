from odoo import models, fields, api

_MESES = [
    ('ene', 1, 'Enero'), ('feb', 2, 'Febrero'), ('mar', 3, 'Marzo'), ('abr', 4, 'Abril'),
    ('may', 5, 'Mayo'), ('jun', 6, 'Junio'), ('jul', 7, 'Julio'), ('ago', 8, 'Agosto'),
    ('sep', 9, 'Septiembre'), ('oct', 10, 'Octubre'), ('nov', 11, 'Noviembre'), ('dic', 12, 'Diciembre'),
]


class WizardDeclaradoMensual(models.TransientModel):
    _name = 've.declarado.mensual.wizard'
    _description = 'Cargar Declarado SENIAT por mes'

    # 12 campos directos (no lineas O2M) -- una lista editable dentro de un
    # wizard modal se quedaba sin ancho para montos de 9+ digitos, y el
    # onchange que reconstruia las lineas terminaba pisando lo que la
    # usuaria estaba escribiendo con los valores guardados (0 si el mes
    # nunca se habia guardado). Bug real 2026-08-07, reportado en vivo:
    # "no registra el monto...deja la mayoria en cero". Con campos propios
    # no hay lineas que reconstruir ni columna que se quede corta.
    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company)
    anio = fields.Integer(
        string='Año', required=True,
        default=lambda self: fields.Date.today().year)

    ene = fields.Float(string='Enero', digits=(16, 2))
    feb = fields.Float(string='Febrero', digits=(16, 2))
    mar = fields.Float(string='Marzo', digits=(16, 2))
    abr = fields.Float(string='Abril', digits=(16, 2))
    may = fields.Float(string='Mayo', digits=(16, 2))
    jun = fields.Float(string='Junio', digits=(16, 2))
    jul = fields.Float(string='Julio', digits=(16, 2))
    ago = fields.Float(string='Agosto', digits=(16, 2))
    sep = fields.Float(string='Septiembre', digits=(16, 2))
    oct = fields.Float(string='Octubre', digits=(16, 2))
    nov = fields.Float(string='Noviembre', digits=(16, 2))
    dic = fields.Float(string='Diciembre', digits=(16, 2))

    def _valores_guardados(self, company_id, anio):
        """{campo_mes: monto} ya guardado en ve.declarado.mensual para esa
        compañía/año -- 0.0 para los meses sin registro todavía."""
        existentes = {
            d.mes: d.monto_declarado
            for d in self.env['ve.declarado.mensual'].search([
                ('company_id', '=', company_id), ('anio', '=', anio),
            ])
        }
        return {campo: existentes.get(mes_num, 0.0) for campo, mes_num, _ in _MESES}

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        company_id = vals.get('company_id') or self.env.company.id
        anio = vals.get('anio') or fields.Date.today().year
        vals.update(self._valores_guardados(company_id, anio))
        return vals

    def action_cargar(self):
        """Recarga explícita (botón, nunca automática) de los valores ya
        guardados para el company_id/año actuales del formulario -- por
        si la usuaria cambió el Año y quiere ver/editar ese otro año."""
        self.ensure_one()
        self.update(self._valores_guardados(self.company_id.id, self.anio))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 've.declarado.mensual.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_guardar(self):
        self.ensure_one()
        Declarado = self.env['ve.declarado.mensual']
        for campo, mes_num, _ in _MESES:
            monto = self[campo]
            existente = Declarado.search([
                ('company_id', '=', self.company_id.id),
                ('anio', '=', self.anio),
                ('mes', '=', mes_num),
            ], limit=1)
            if existente:
                existente.monto_declarado = monto
            else:
                Declarado.create({
                    'company_id': self.company_id.id,
                    'anio': self.anio,
                    'mes': mes_num,
                    'monto_declarado': monto,
                })
        return {'type': 'ir.actions.act_window_close'}
