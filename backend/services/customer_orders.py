"""Additive local-demo customer profile and order APIs. No real fulfillment or payment actions."""
from __future__ import annotations
import re, sqlite3, uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from backend.db.store import connect
from backend.auth.security import principal

router = APIRouter(tags=['Customer profiles and demo orders'])

class ProfileIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    phone: str | None = Field(default=None, max_length=20)


def migrate():
    with connect() as db:
        cols = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
        for name, definition in [('full_name', 'TEXT'), ('phone', 'TEXT')]:
            if name not in cols:
                db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS demo_orders (
          id TEXT PRIMARY KEY, order_code TEXT NOT NULL UNIQUE,
          customer_id TEXT NOT NULL REFERENCES users(id),
          placed_at TEXT NOT NULL, status TEXT NOT NULL,
          currency TEXT NOT NULL DEFAULT 'INR', total_amount REAL NOT NULL CHECK(total_amount>=0)
        );
        CREATE TABLE IF NOT EXISTS ticket_order_links (
          ticket_id TEXT PRIMARY KEY REFERENCES tickets(id),
          order_code TEXT NOT NULL, customer_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_order_items (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES demo_orders(id),
          sku TEXT NOT NULL, product_name TEXT NOT NULL,
          quantity INTEGER NOT NULL CHECK(quantity>0),
          unit_price REAL NOT NULL CHECK(unit_price>=0)
        );
        CREATE INDEX IF NOT EXISTS idx_demo_orders_owner ON demo_orders(customer_id,placed_at);
        ''')

@router.get('/account/profile')
def profile(user=Depends(principal)):
    with connect() as db:
        r=db.execute('SELECT id,email,role,verified,public_id,full_name,phone FROM users WHERE id=?',(user['id'],)).fetchone()
        if not r: raise HTTPException(404,'Account not found')
        return dict(r)

@router.put('/account/profile')
def update_profile(body:ProfileIn,user=Depends(principal)):
    name=' '.join(body.full_name.strip().split())
    phone=(body.phone or '').strip() or None
    if len(name)<2: raise HTTPException(422,'Full name required')
    if phone and not re.fullmatch(r'\+?[0-9 ()-]{7,20}',phone): raise HTTPException(422,'Invalid phone')
    with connect() as db:
        db.execute('UPDATE users SET full_name=?,phone=? WHERE id=?',(name,phone,user['id']))
    return {'updated':True}

@router.get('/account/orders')
def my_orders(user=Depends(principal)):
    if user['role']!='customer': raise HTTPException(403,'Customer account required')
    with connect() as db:
        orders=[dict(r) for r in db.execute('SELECT id,order_code,placed_at,status,currency,total_amount FROM demo_orders WHERE customer_id=? ORDER BY placed_at DESC',(user['id'],))]
        for order in orders:
            order['items']=[dict(r) for r in db.execute('SELECT sku,product_name,quantity,unit_price FROM demo_order_items WHERE order_id=?',(order['id'],))]
            order.pop('id')
        return orders

class DemoSeedIn(BaseModel):
    confirmation: str

@router.post('/account/orders/seed-demo',status_code=201)
def seed_demo_orders(body:DemoSeedIn,user=Depends(principal)):
    import os
    if os.getenv('FIXFLOW_LOCAL_DEMO')!='1': raise HTTPException(403,'Local demo only')
    if user['role']!='customer': raise HTTPException(403,'Customer required')
    if body.confirmation!='CREATE_FICTIONAL_ORDERS': raise HTTPException(422,'Confirmation required')
    examples=[('2026-10-24','DELIVERED',[('NC-HDP-101','Wireless Headphones',1,1499.00),('NC-CBL-012','USB-C Charging Cable',1,299.00)]),('2026-10-27','SHIPPED',[('NC-BAG-210','Everyday Backpack',1,899.00)]),('2026-10-29','PROCESSING',[('NC-MUG-030','Ceramic Coffee Mug',2,249.00)])]
    with connect() as db:
        count=db.execute('SELECT COUNT(*) FROM demo_orders WHERE customer_id=?',(user['id'],)).fetchone()[0]
        if count: return {'created':0,'message':'Existing demo orders retained'}
        for n,(date,status,items) in enumerate(examples,1):
            oid=str(uuid.uuid4()); code=f'DEMO-{user["id"][:8].upper()}-{n:03d}'
            total=round(sum(q*price for _,_,q,price in items),2)
            db.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',(oid,code,user['id'],date,status,total))
            for sku,name,qty,price in items:
                db.execute('INSERT INTO demo_order_items VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),oid,sku,name,qty,price))
    return {'created':len(examples)}
