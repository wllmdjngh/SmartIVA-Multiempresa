# ve_retencion_iva — Retenciones IVA Venezuela (DJCS)

**Versión:** 19.0.2.0.0 | **Marco legal:** PA SNAT/2025/000054

Gestión completa del ciclo de vida de retenciones IVA de clientes (Agentes SPE) para empresas venezolanas en Odoo 19.

---

## Menú

```
Contabilidad → DEMO Impuestos
├── IVA Clientes
│   ├── Libro de Ventas
│   ├── Buzón de Comprobantes
│   ├── Retenciones IVA
│   ├── Retenciones SENIAT
│   ├── Conciliación SENIAT
│   ├── Conciliación Visual
│   ├── Configuración IVA       ← solo administradores
│   └── Reiniciar Demo          ← solo administradores
├── IVA Proveedores             ← pendiente
└── Declaración IVA
```

---

## Flujo principal

```
Factura confirmada → Retención "Esperado" (RET-IVA-C/YYYY/NNNN)
    → Comprobante recibido (manual / OCR email / OCR wizard)
    → Recibido (borrador)
    → Confirmar (crea asiento contable)
    → Confirmado
    → Conciliar SENIAT (match con datos del portal)
    → En Revisión → Aprobar
    → Conciliado (con monto C.66 → Aprobado para Declarar)
    → Declaración IVA → Reporte 030 / Declarar (RPA)
    → Declarado
```

---

## Configuración de parámetros

### ✅ Obligatorios — Cuentas Contables

Configurar en: **IVA Clientes → Configuración IVA** (o Ajustes → Contabilidad)

| Parámetro del sistema | Descripción | Valor QA / Prod |
|---|---|---|
| `ve_retencion_iva.cuenta_iva_retenido_cobrar_id` | ID de cuenta IVA Retenido por Cobrar (débito al confirmar) | Auto: busca código `1151004` |
| `ve_retencion_iva.cuenta_iva_por_pagar_id` | ID de cuenta IVA por Pagar (crédito al confirmar) | Auto: busca código `2172003` |

> **Nota:** El `post_init_hook` configura estos automáticamente al instalar si existen las cuentas con los códigos `1151004` (I.V.A. CREDITO FISCAL) y `2172003` (I.V.A DEBITO FISCAL). Si las cuentas tienen otros códigos, configurar manualmente seleccionando la cuenta en el menú.

---

### 🤖 OCR — Lectura automática de comprobantes

Configurar en: **Ajustes → Técnico → Parámetros del sistema**

| Parámetro | Descripción | Dónde obtener |
|---|---|---|
| `ve_retencion_iva.anthropic_api_key` | API Key de Claude (Anthropic) — OCR primario | console.anthropic.com |
| `ve_retencion_iva.google_vision_api_key` | API Key de Google Vision — OCR secundario (opcional) | console.cloud.google.com |

**Orden de fallback OCR:** Claude Vision → Google Vision → Odoo IAP → Tesseract

> El OCR se activa al enviar un PDF/imagen al alias de email o al usar el wizard "Adjuntar Comprobante".

---

### 🔌 RPA — Robot de extracción SENIAT y declaración

Configurar en: **Ajustes → Técnico → Parámetros del sistema**

| Parámetro | Descripción | Ejemplo |
|---|---|---|
| `ve_retencion_iva.rpa_base_url` | URL base del servidor RPA | `https://rpa.empresa.com` |
| `ve_retencion_iva.rpa_username` | Usuario SENIAT del contribuyente | `J-12345678-9` |
| `ve_retencion_iva.rpa_password` | Contraseña portal SENIAT | `••••••••` |
| `ve_retencion_iva.rpa_api_key` | API Key que el RPA usa para callbacks al Odoo | clave larga aleatoria |
| `ve_retencion_iva.rpa_declaracion_url` | Endpoint RPA para declarar | `https://rpa.empresa.com/declarar` |

**Endpoints que expone Odoo al RPA:**

| Método | Ruta | Función |
|---|---|---|
| `POST` | `/api/ve/seniat/cargar_retenciones` | El RPA entrega retenciones descargadas del SENIAT |
| `POST` | `/api/ve/declaracion/ejecutar` | Odoo solicita al RPA ejecutar la declaración |
| `POST` | `/api/ve/declaracion/registrar_resultado` | El RPA confirma el resultado de la declaración |

Autenticación: header `X-API-Key` con el valor de `ve_retencion_iva.rpa_api_key`.

---

### ⚙️ Operación general

| Parámetro | Descripción | Default |
|---|---|---|
| `ve_retencion_iva.dias_aviso_vencimiento` | Días de anticipación para recordatorios de vencimiento (cron diario) | `3` |

---

### 📧 Email — Buzón de comprobantes

| Item | Valor |
|---|---|
| Alias de entrada | `comprobantes-iva@{dominio-odoo}` |
| Ejemplo QA | `comprobantes-iva@djcs-qa-32944865.dev.odoo.com` |

> Enviar el comprobante como **nuevo mensaje** (no como reply) para crear un registro nuevo en el Buzón.

---

## Secuencias auto-generadas

| Secuencia | Código | Formato | Ejemplo |
|---|---|---|---|
| Retenciones IVA Clientes | `ve.wh.iva` | `RET-IVA-C/YYYY/NNNN` | `RET-IVA-C/2026/0001` |
| Retenciones IVA Proveedores (futuro) | `ve.wh.iva.prov` | `RET-IVA-P/YYYY/NNNN` | `RET-IVA-P/2026/0001` |

---

## Grupos de seguridad

| Grupo | Acceso |
|---|---|
| `group_ret_iva_usuario` | Leer retenciones, subir comprobantes |
| `group_ret_iva_gestor` | Confirmar, conciliar, exportar, aprobar |
| Contabilidad Manager | Todo |
| Administrador | Todo + demo + configuración |

---

## Cron configurado

| Nombre | Frecuencia | Función |
|---|---|---|
| Retenciones IVA: Recordatorios de Vencimiento | Diario | Envía recordatorios a clientes SPE con comprobantes próximos a vencer |

---

## Notas técnicas

- `account.account` en Odoo 19: campo `company_ids` (Many2many), no `company_id`
- `message_post` con HTML: siempre usar `Markup` + `escape()` para valores dinámicos
- One2many domains en vistas Odoo 19: se aplican en la presentación, no en Python
- `ir.cron` en Odoo 19: no tiene campo `numbercall`
- `res.config.settings` en Odoo 19: el xpath `//div[@id='account']` no existe; usar formulario standalone
- `fa-robot` no existe en FA4 (Odoo usa FA4) → usar `fa-android`
- Wizard `required=True` en modelo bloquea el `create()` antes de abrir el form → validar en `action_confirmar` con `UserError`, dejar `required="1"` solo en la vista
- `_compute_reporte_seniat`: C.40/C.42/C.43/C.443 se calculan desde `account.move` por rango `fecha_inicio`/`fecha_fin` (igual que Libro de Ventas). El C.66 usa `wh_iva_ids`. El C.33 usa `seniat_ids` como placeholder hasta implementar IVA Proveedores.
- Sum de listas agrupadas en Odoo: el agregado del encabezado de grupo es SQL calculado al expandir el grupo — no se refresca automáticamente al editar registros inline. El valor guardado en BD sí es correcto.

---

## IVA Proveedores — Arquitectura planificada (Sprint F)

*Estado actual: placeholder en menú. Implementación pendiente.*

### Qué hace

Registra el IVA de compras y los comprobantes de retención que proveedores SPE
emiten a la empresa, y alimenta C.31–C.34 de la Forma 30 con datos reales.

### Subsprints

| # | Tarea | Modelos / Archivos | Prioridad |
|---|---|---|---|
| F.1 | Campo 33 desde facturas de compra | `ve_conciliacion.py` `_compute_reporte_seniat` | Alta |
| F.2 | Libro de Compras (PDF + Excel) | `wizard_libro_compras.py` + `ve_libro_compras.xml` | Alta |
| F.3 | Comprobantes de retención recibidos | Modelo nuevo `ve.wh.iva.prov` | Media |

### F.1 — Fix campo_33 (análogo al fix de campo_42 aplicado en 2026-06-07)

Reemplazar en `_compute_reporte_seniat`:
```python
# Placeholder actual (incorrecto — usa seniat_ids)
rec.campo_33_base    = sum(rec.seniat_ids.mapped('monto_base'))
rec.campo_33_credito = sum(s.monto_base * (s.alicuota or 16.0) / 100 ...)
```
Por un `search` sobre `account.move` (in_invoice/in_refund) filtrado por
`fecha_inicio`/`fecha_fin`, misma lógica de alícuotas que campo_42.

### F.2 — Libro de Compras

Modelo: `ve.wizard.libro.compras` (TransientModel, mismo patrón que `ve.wizard.libro.ventas`).

Fuente de datos: `account.move` con `move_type in ('in_invoice', 'in_refund')`.

Columnas: Op. N° · Fecha · RIF Proveedor · Nombre · N° Comprobante Ret. ·
N° Factura · N° Control · Total c/IVA · Base 16% · IVA 16% · Base 8% · IVA 8% ·
Base Exenta · IVA Retenido (por proveedor SPE).

### F.3 — Modelo `ve.wh.iva.prov`

Registra comprobantes de retención que proveedores SPE entregan a la empresa.
Secuencia: `RET-IVA-P/YYYY/NNNN` (ya definida en `ir.sequence`).

| Campo | Tipo | Descripción |
|---|---|---|
| `ref` | Char | Referencia interna RET-IVA-P/YYYY/NNNN |
| `name` | Char | N° comprobante 14 dígitos del proveedor |
| `partner_id` | M2o res.partner | Proveedor SPE |
| `invoice_id` | M2o account.move | Factura de compra vinculada |
| `periodo` | Char | Período yyyy-mm |
| `fecha` | Date | Fecha del comprobante |
| `monto_base` | Float | Base imponible 16% |
| `monto_iva` | Float | IVA 16% |
| `monto_base_red` | Float | Base 8% |
| `monto_iva_red` | Float | IVA 8% |
| `monto_retenido` | Float | Monto retenido por el proveedor |
| `state` | Selection | esperado → recibido → confirmado |
| `conciliacion_id` | M2o | Período de Declaración IVA |

Estos comprobantes alimentan C.67 (retenciones recibidas de proveedores SPE) en
la Forma 30. La conciliación SENIAT de compras puede hacerse en una fase posterior.

---

## Referencia de estados

El módulo maneja 4 campos de estado independientes en 2 modelos distintos.

### 1. `ve.wh.iva` → `state` — Ciclo de vida del comprobante

| Valor | Etiqueta | Qué significa | Quién lo asigna |
|---|---|---|---|
| `esperado` | No Recibido | Odoo espera recibir el comprobante del agente | Al crear la retención |
| `vencido` | Vencido | Plazo legal expiró sin recibir el comprobante | Cron nocturno |
| `borrador` | Recibido | Comprobante físico recibido, pendiente de confirmación | `action_recibir()` |
| `confirmado` | Confirmado | Confirmado + asiento contable creado; listo para conciliar SENIAT | `action_confirmar()` |
| `conciliado` | Conciliado | Cuadrado con SENIAT + período aprobado; disponible para C.66 si aplica | `action_aprobar()` |
| `declarado` | Declarado | C.66 incluido en declaración IVA presentada — bloqueado, no reutilizable | `action_marcar_declarado()` / RPA |
| `anulado` | Anulado | Cancelado; asiento revertido si existía | `action_anular()` |

**Reglas del estado `conciliado`:**
- Comprobantes con `incluir_declaracion=True` → pasan a `declarado` al declarar el período.
- Comprobantes con `incluir_declaracion=False` → **quedan en `conciliado`** y son considerados en el siguiente período de conciliación.

**Reglas del estado `declarado`:**
- Completamente bloqueado (no editable).
- Excluido de búsquedas de conciliación en períodos futuros.
- Solo visible en la pestaña **Comprobantes Declarados** del período ya cerrado.

> **Implementado** en commit `0b6b296` — `action_marcar_declarado()` y callback RPA marcan los comprobantes C.66=✓ como `declarado`.

---

### 2. `ve.wh.iva` → `estado_conciliacion` — Resultado del match SENIAT vs Odoo

Participan en el match SENIAT: `confirmado`, `esperado`, `vencido`.
- `borrador`: excluido — debe confirmarse primero.
- `declarado`: excluido — ya cerrado.

**Distinción clave:** si hay match SENIAT, el `estado_conciliacion` diferencia si el comprobante fue recibido o no — lo que determina si suma al C.66.

| Valor | Etiqueta | Cuándo | `state` / C.66 |
|---|---|---|---|
| `pendiente` | Por Conciliar | Antes de conciliar o `borrador` sin confirmar | cualquiera / — |
| `solo_odoo` | Sin SENIAT | No encontró coincidencia en SENIAT por RIF+Control | cualquiera / ✗ |
| `solo_seniat` | Solo en SENIAT | SENIAT lo tiene pero no existe retención Odoo (excepción manual) | — |
| `diferencia` | Diferencia de Monto | Match encontrado pero montos distintos | cualquiera / ✗ |
| `conciliada_norec` | No Recibido SENIAT OK | Match OK + `esperado`/`vencido` — informativo, `monto_recibido=0` no suma C.66 | `esperado`/`vencido` / ✗ |
| `listo_declarar` | Listo para Declarar | Match OK + `confirmado` recibido. C.66=✗ → próximo período | `confirmado`/`conciliado` / ✓ |
| `declarado` | Declarado | C.66 incluido en declaración IVA presentada al SENIAT | `declarado` / ✓ |
| `conciliada` | Conciliada | *Legacy — no se asigna en código nuevo* | — |
| `aprobado_declarar` | Aprobado para Declarar | *Legacy — no se asigna en código nuevo* | — |

> Aparece en pestañas **No Recibidas** y **Recibidas** como columna "Estado Conciliación".

---

### 3. `ve.wh.iva` → `estado_visual` — Conciliación Visual (computed)

Combinación de `state` + `estado_conciliacion`. Matriz 6 × 3:

Todos participan en el match SENIAT excepto `borrador` y `declarado`. El `estado_conciliacion` diferencia recibido vs no recibido, lo que determina si suma al C.66.

| `state` | Sin SENIAT | Dif. monto | SENIAT OK |
|---|---|---|---|
| `vencido` | 🔴 `vencido_sin_seniat` | 🔴 `vencido_dif_seniat` | 🟠 `vencido_seniat_ok` |
| `esperado` | 🔵 `no_rec_sin_seniat` | 🟠 `no_rec_dif_seniat` | 🔵 `no_rec_seniat_ok` |
| `borrador` *(cualquier caso)* | ⚫ `rec_borrador` | — | — |
| `confirmado` + diff comprobante cliente | 🟠 `dif_cli_sin_seniat` | 🟠 `dif_cli_dif_seniat` | 🟠 `dif_cli_seniat_ok` |
| `confirmado` sin diff cliente | 🔵 `conf_sin_seniat` | 🟠 `conf_dif_seniat` | 🟢 `conf_seniat_ok` |
| `conciliado` | 🔵 `rec_sin_seniat` | 🟠 `rec_dif_seniat` | 🟢 `rec_seniat_ok` |

*`anulado` y `pendiente` → ⚫ gris (fallback)*

**Colores:** 🔴 danger (rojo) · 🟠 warning (naranja) · 🔵 info (azul) · 🟢 success (verde) · ⚫ muted (gris)

**Reglas clave:**
- `vencido`/`esperado`: participan en match. SENIAT OK → `vencido_seniat_ok`/`no_rec_seniat_ok` pero `monto_c66=0` (no suman al C.66).
- `borrador`: siempre ⚫ gris — excluido del match, debe confirmarse primero.
- `dif_cli_*`: solo `confirmado` cuando `comp_monto_retenido ≠ monto_retenido`.
- `conciliado`: hereda el resultado del match que tenía cuando era `confirmado`.

> Solo aparece en la pestaña **Conciliación Visual** (períodos no declarados).

---

### 4. `ve.seniat.retencion` → `estado` — Registros descargados del SENIAT

Modelo completamente separado (`ve.seniat.retencion`). Pestaña **Retenciones SENIAT** (solo visible cuando el período NO está declarado). No necesita estado `declarado` — la pestaña se oculta al declarar el período.

| Valor | Etiqueta | Qué significa | Espejo en `estado_conciliacion` |
|---|---|---|---|
| `cargado` | Por Conciliar | Cargado desde CSV/RPA, aún no comparado con Odoo | `pendiente` |
| `conciliado` | Conciliado | Encontró su par en Odoo con monto igual | `listo_declarar` / `conciliada_norec` |
| `diferencia` | Con Diferencia | Encontró par en Odoo pero montos distintos | `diferencia` |
| `sin_match` | Sin Odoo | No existe retención Odoo con ese RIF+Control | `solo_seniat` (excepción) |

---

### 5. Flujo de cambios por acción

| Acción | `ve.wh.iva.state` | `ve.wh.iva.estado_conciliacion` | `ve.seniat.retencion.estado` |
|---|---|---|---|
| Crear retención | `esperado` | `pendiente` | — |
| Cron vencimiento | → `vencido` | sin cambio | — |
| Recibir comprobante | → `borrador` | sin cambio | — |
| Confirmar | → `confirmado` | sin cambio | — |
| Cargar XLSX/RPA | sin cambio | sin cambio | `cargado` |
| **Conciliar SENIAT** | sin cambio (`borrador` excluido) | `confirmado` → `solo_odoo`/`diferencia`/`listo_declarar` · `esperado`/`vencido` → `solo_odoo`/`diferencia`/`conciliada_norec` · `borrador` → permanece `pendiente` | → `conciliado` / `diferencia` / `sin_match` |
| **Aprobar período** | `confirmado` → `conciliado` | sin cambio | sin cambio |
| **Declarar IVA** | C.66=✓: `conciliado` → `declarado` · C.66=✗: permanece `conciliado` (próximo período) | C.66=✓: `listo_declarar` → `declarado` · C.66=✗: permanece `listo_declarar` | sin cambio |

---

### 6. Qué muestra cada pestaña según estado del período

| Pestaña | Visible cuando | Registros mostrados | Columna estado |
|---|---|---|---|
| Conciliación Visual | período ≠ `declarado` | Todos (no anulados) | `estado_visual` (computed) |
| No Recibidas | período ≠ `declarado` | `state in ('esperado','vencido')` | `estado_conciliacion` |
| Recibidas | período ≠ `declarado` | `state in ('borrador','confirmado','conciliado')` | `estado_conciliacion` |
| Retenciones SENIAT | período ≠ `declarado` | modelo `ve.seniat.retencion` | `estado` (modelo distinto) |
| **Comprobantes Declarados** | período = `declarado` | `state = 'declarado'` | botón abrir comprobante |

---

## Dashboard IVA — Arquitectura (Sprint REQ-14) *(pendiente build)*

Vista form singleton `ve.dashboard.iva` accesible desde "Dashboard IVA" en el menú principal.

### KPIs gerenciales (stat buttons con drill-down)

| KPI | Cálculo | Fuente | Drill-down |
|-----|---------|--------|------------|
| Margen C/D | `campo_66 / campo_49` del último período aprobado | `ve.conciliacion.periodo` | Form período activo |
| Tasa Efectiva Ret. | `Σ monto_iva_retenido / Σ monto_base_imponible` (año actual) | `ve.wh.iva` | Lista filtrada año |
| Cumplimiento SPE | % períodos declarados en plazo (últimos 24 quincenas) | `ve.conciliacion.periodo` | Lista con columna cumplimiento |
| Sanciones Año | `Σ monto_total_bs` estado pendiente/impugnada (año actual) | `ve.sancion.iva` | Lista sanciones año |

### Semáforo operativo (stat buttons rápidos)

| Botón | Color | Dato |
|-------|-------|------|
| N Vencidos | 🔴 danger | count `ve.wh.iva` state=vencido |
| N Esperados | 🟠 warning | count `ve.wh.iva` state=esperado |
| N Períodos Abiertos | 🔵 info | count períodos sin declarar en ventana prescripción |
| Riesgo Est. EUR | 🔴/🟠 | suma estimación multas desde wizard |

---

## Sanciones IVA — Modelos (Sprint REQ-16) *(pendiente build)*

### `ve.sancion.iva` — Cabecera de sanción

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `name` | Char | Descripción (ej. "Resolución SNAT-2026-0031") |
| `numero_resolucion` | Char | N° oficial de la resolución SENIAT |
| `fecha` | Date | Fecha de la resolución |
| `tipo_origen` | Selection | `periodo_especifico` · `auditoria_fiscal` · `autoliquidacion` |
| `es_agente_retencion` | Boolean | True → prescripción 6 años; False → 10 años (Art. 62 COT) |
| `fecha_prescripcion` | Date computed | `fecha` + 6 o 10 años |
| `estado` | Selection | `pendiente` · `impugnada` · `pagada` · `prescrita` |
| `fecha_vencimiento_pago` | Date | Plazo para pagar sin intereses adicionales |
| `fecha_pago` | Date | Fecha efectiva de pago |
| `monto_total_bs` | Float computed | Σ `monto_bs` líneas |
| `monto_total_eur` | Float computed | Σ `monto_eur` líneas |
| `line_ids` | O2M → `ve.sancion.iva.line` | Desglose por concepto |
| `note` | Text | Notas, recurso, fundamento legal |

### `ve.sancion.iva.line` — Líneas de detalle

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `sancion_id` | M2o `ve.sancion.iva` | Cabecera |
| `periodo_id` | M2o `ve.conciliacion.periodo` | Período afectado (opcional) |
| `periodo_desc` | Char | Período en texto libre (si no hay M2o) |
| `tipo` | Selection | `ilicito_formal` · `omision` · `rechazo_credito` · `interes_moratorio` · `multa_forma` |
| `descripcion` | Char | Detalle del concepto |
| `cantidad` | Integer | N° de comprobantes/períodos afectados |
| `monto_bs` | Float | Monto en Bs (según resolución) |
| `tasa_eur_bcv` | Float | Tasa EUR/BCV al registrar (de `res.currency.rate`) |
| `monto_eur` | Float computed | `monto_bs / tasa_eur_bcv` |
| `wh_iva_ids` | M2M `ve.wh.iva` | Retenciones vinculadas (trazabilidad) |

### Tipos de sanción (COT 2020)

| `tipo` | Art. COT | Descripción |
|--------|----------|-------------|
| `ilicito_formal` | Art. 101 | No entrega de comprobante dentro del plazo (por comprobante) |
| `omision` | Art. 111 | Omisión de pago del tributo (10–25% del monto omitido) |
| `rechazo_credito` | Art. 94 | Rechazo crédito fiscal por comprobante irregular |
| `interes_moratorio` | Art. 66 | Interés sobre tributo no pagado (tasa activa BCV) |
| `multa_forma` | Art. 100 | Omisión de declaración, errores de forma |

### Prescripción (Art. 62 COT)

| Condición | Ventana | Desde |
|-----------|---------|-------|
| Empresa es Agente de Retención SPE | 6 años | Fin del período fiscal |
| Contribuyente ordinario (no SPE) | 10 años | Fin del período fiscal |

---

## Estimador de Riesgo SENIAT — Wizard `ve.wizard.estimacion.riesgo` *(pendiente build)*

Calcula la **exposición máxima estimada** ante una auditoría del SENIAT, consultando comprobantes vencidos y períodos sin declarar dentro de la ventana de prescripción.

### Parámetros del sistema requeridos

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `ve_retencion_iva.es_agente_retencion` | Empresa calificada como Agente de Retención SPE | `True` |
| `ve_retencion_iva.sancion_por_comprobante_bs` | Multa COT Art.101 por comprobante no entregado (Bs) | `0.0` (configurar) |
| `ve_retencion_iva.porcentaje_omision` | % sanción omisión declaración (COT Art.111) | `25.0` |

### Lógica de cálculo

```
ventana = 6 años si es_agente_retencion else 10 años
fecha_corte = hoy - ventana

períodos_en_riesgo = ve.conciliacion.periodo donde:
    fecha_hasta >= fecha_corte AND estado != 'declarado'

para cada período:
    n_riesgo = count(ve.wh.iva donde state in ('esperado','vencido'))
    sancion_ilicito = n_riesgo × sancion_por_comprobante_bs
    sancion_omision = campo_49 × porcentaje_omision  (si no declarado)
    total_periodo = sancion_ilicito + sancion_omision

tasa_eur = res.currency.rate más reciente para EUR
riesgo_total_bs = Σ total_periodo
riesgo_total_eur = riesgo_total_bs / tasa_eur
```

### Resultado presentado

Tabla con una fila por período:

| Período | N° en Riesgo | Sanción Ilícito Bs | Sanción Omisión Bs | Total Período Bs | Total EUR |
|---------|-------------|-------------------|-------------------|-----------------|-----------|

Totales al pie + semáforo de riesgo:
- 🔴 Alto: riesgo_total_eur > umbral configurable
- 🟠 Medio: entre 50% y 100% del umbral
- 🟢 Bajo: < 50% del umbral

> **Nota importante:** Los montos son estimaciones máximas (peor caso). El SENIAT puede aplicar criterios distintos, rebajas por pago voluntario, o diferir el cálculo a la fecha de notificación. Usar como herramienta de planificación, no como liquidación definitiva.
