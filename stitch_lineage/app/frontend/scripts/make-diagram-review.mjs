// Reproducible, entirely synthetic data for the diagram review screenshots.
// node scripts/make-diagram-review.mjs /tmp/stitch-demo-graph.json
import { writeFileSync } from 'node:fs'
const nodes = [], edges = []
const model = (name, schema = 'marts') => {
  const id = `model.demo.${name}`
  nodes.push({ node_id: id, node_type: 'model', name, schema, table: name, properties: { materialization: 'table' } })
  return id
}
const column = (table, name) => {
  const id = `${table}::${name}`
  nodes.push({ node_id: id, node_type: 'column', name, column: name, data_type: 'number', schema: nodes.find(n => n.node_id === table).schema, table: table.split('.').at(-1), properties: {} })
  return id
}
const link = (from, to, edge_type = 'feeds', confidence = 'exact') => edges.push({ from, to, edge_type, confidence, evidence: {} })
const raw = model('stg_payments', 'staging'), orders = model('fct_orders'), customers = model('dim_customers')
const daily = model('fct_revenue_daily'), monthly = model('mart_revenue_monthly'), viz = model('viz_orders', 'reporting')
const rawAmount = column(raw, 'net_revenue'), amount = column(orders, 'net_revenue'), vizAmount = column(viz, 'net_revenue')
link(rawAmount, amount, 'feeds', 'parsed'); link(amount, vizAmount)
link(column(orders, 'customer_id'), column(customers, 'customer_id'), 'relates_to', 'validated')
for (const table of [orders, customers]) for (let i=1;i<=42;i++) column(table, `attribute_${String(i).padStart(2,'0')}`)
for (const name of ['net_revenue', 'gross_revenue', 'refund_amount', 'recurring_revenue', 'one_time_revenue', 'discount_amount', 'tax_amount', 'revenue_per_order']) {
  const day = column(daily, name), month = column(monthly, name)
  link(amount, day, 'feeds', 'inferred'); link(day, month)
}
nodes.push({node_id:'mb_field::1',node_type:'mb_field',name:'Net Revenue',properties:{}})
link(vizAmount,'mb_field::1','binds_to')
for(let i=1;i<=3;i++) nodes.push({node_id:`mb_dash::${i}`,node_type:'mb_dashboard',name:['Board KPIs','Revenue review','Finance overview'][i-1],properties:{}})
for(let i=1;i<=6;i++) {
  nodes.push({node_id:`mb_card::${i}`,node_type:'mb_card',name:['Revenue by country','Net revenue by month','Revenue by store','Revenue trend','Revenue by product','Revenue forecast'][i-1],properties:{collection_name:'Revenue'}})
  link('mb_field::1',`mb_card::${i}`,'consumed_by'); link(`mb_card::${i}`,`mb_dash::${1+(i-1)%3}`,'appears_on')
}
writeFileSync(process.argv[2] ?? '/tmp/stitch-demo-graph.json', JSON.stringify({schema_version:1,generated_at:'2026-10-22T10:00:00Z',nodes,edges,coverage:{}}))
