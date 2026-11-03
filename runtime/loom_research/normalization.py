"""Typed, versioned normalization views; raw values and uncertainty survive.

Canonical comparison forms never replace native evidence or merge identities.
Locale, currency, century and numbering policies are explicit profile choices.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from email.utils import parsedate_to_datetime
from fractions import Fraction
import argparse
import json
from pathlib import Path
import re
import unicodedata
from .contracts import fields, require, result, digest, text, integer, read_json

VERSION='1.0.1'
KINDS={'text','name','business_name','identifier','email','phone','date','datetime','percentage',
       'number','money','money_stage','formula','empty','address'}
PROFILE_KEYS=('decimal_separator','group_separator','date_order','two_digit_century',
              'currency','dollar_currency','phone_country','identifier_separators',
              'email_casefold_domains','identifier_width','percent_input')
PRECISION={'USD':2,'CAD':2,'AUD':2,'EUR':2,'GBP':2,'JPY':0,'KWD':3}
PUNCT=str.maketrans({'’':"'",'‘':"'",'‐':'-','‑':'-','–':'-','−':'-'})
MONTHS={name:i for i,name in enumerate(('january','february','march','april','may','june','july','august','september','october','november','december'),1)}
STAGES={'requested':'requested','amount requested':'requested','authorized':'authorized',
        'funded':'funded','received':'received','pooled':'pooled','paid':'paid','applied':'applied',
        'credited':'credited','credit applied':'credited','reimbursed':'reimbursed',
        'refunded':'refunded','retained':'retained','claimed damages':'claimed_loss','claimed loss':'claimed_loss'}
FUNCTIONS={'normalize_value','normalize_record'}


def profile(raw=None):
    raw=deepcopy({} if raw is None else raw);fields(raw,(),PROFILE_KEYS)
    require(raw.get('decimal_separator','.') in ('.',','),'decimal separator must be . or ,')
    group=raw.get('group_separator',',');require(group in (',','.',' ','') and group!=raw.get('decimal_separator','.'),'group separator conflicts with decimal separator')
    require(raw.get('date_order') in (None,'MDY','DMY','YMD'),'date_order must be explicit MDY/DMY/YMD')
    century=raw.get('two_digit_century');require(century is None or type(century) is int and century in (1800,1900,2000,2100),'two-digit years need an explicit century')
    for key in ('currency','dollar_currency'):
        require(raw.get(key) is None or isinstance(raw[key],str) and raw[key] in PRECISION,'unsupported explicit currency')
    require(raw.get('dollar_currency') in (None,'USD','CAD','AUD'),'dollar_currency requires USD/CAD/AUD')
    require(raw.get('phone_country') in (None,'US','CA'),'phone country must be US/CA or omitted')
    require(type(raw.get('identifier_separators',False)) is bool,'identifier_separators must be boolean')
    if 'identifier_width' in raw:integer(raw['identifier_width'],1,64,'identifier width')
    require(raw.get('percent_input','percent') in ('percent','ratio'),'percent_input must be percent or ratio')
    domains=raw.get('email_casefold_domains',[])
    require(isinstance(domains,list) and len(domains)<=32 and all(isinstance(x,str) and x==x.lower() for x in domains),'explicit lower-case email domains required')
    return raw


def _space(value):
    return ' '.join(unicodedata.normalize('NFC',value).translate(PUNCT).split())


def _number(value,p):
    if type(value) is bool or type(value) is float:return None,'EXACT_TEXT_REQUIRED'
    raw=str(value).strip().translate(PUNCT);negative=False
    if len(raw)>128:return None,'NUMERIC_DIGIT_BUDGET'
    if raw.startswith('(') and raw.endswith(')'):negative=True;raw=raw[1:-1].strip()
    if raw.startswith(('+','-')):
        if negative:return None,'CONFLICTING_SIGN'
        negative=raw[0]=='-';raw=raw[1:]
    dec=p.get('decimal_separator','.');group=p.get('group_separator',',')
    parts=raw.split(dec)
    if len(parts)>2 or len(parts)==2 and not re.fullmatch(r'[0-9]+',parts[1]):return None,'INVALID_DECIMAL'
    whole=parts[0]
    if group and group in whole:
        if not re.fullmatch(r'[0-9]{1,3}(?:'+re.escape(group)+r'[0-9]{3})+',whole):return None,'INVALID_GROUPING'
        whole=whole.replace(group,'')
    if not re.fullmatch('[0-9]+',whole):return None,'INVALID_NUMBER'
    canonical=('-' if negative else '')+whole+('.'+parts[1] if len(parts)==2 else '')
    try:v=Decimal(canonical)
    except InvalidOperation:return None,'INVALID_NUMBER'
    rendered=format(v,'f')
    if '.' in rendered:rendered=rendered.rstrip('0').rstrip('.')
    if rendered=='-0':rendered='0'
    return rendered,'NORMALIZED'


def _date(raw,p):
    raw=_space(raw);candidates=[]
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',raw):
        try:return date.fromisoformat(raw).isoformat(),'NORMALIZED',[]
        except ValueError:return None,'INVALID_DATE',[]
    reverse=re.fullmatch(r'([0-9]{1,2})\s+([A-Za-z]+),?\s+([0-9]{4})',raw,re.I)
    if reverse:raw=reverse[2]+' '+reverse[1]+' '+reverse[3]
    words=re.fullmatch(r'([A-Za-z]+)\s+([0-9]{1,2})(?:st|nd|rd|th)?,?\s+([0-9]{4})',raw,re.I)
    if words:
        month=next((n for m,n in MONTHS.items() if words[1].lower() in (m,m[:3])),None)
        if month:
            try:return date(int(words[3]),month,int(words[2])).isoformat(),'NORMALIZED',[]
            except ValueError:return None,'INVALID_DATE',[]
    numeric=re.fullmatch(r'([0-9]{1,4})[/-]([0-9]{1,2})[/-]([0-9]{1,4})',raw)
    if not numeric:return None,'UNPARSED_DATE',[]
    bits=[int(x) for x in numeric.groups()];order=p.get('date_order')
    orders=[order] if order else ['YMD'] if len(numeric[1])==4 else ['MDY','DMY']
    for kind in orders:
        mapped=dict(zip(kind,bits));year=mapped['Y'];year_text=numeric[kind.index('Y')+1]
        if len(year_text)<4:
            if p.get('two_digit_century') is None:return None,'AMBIGUOUS_CENTURY',[]
            year+=p['two_digit_century']
        try:candidates.append(date(year,mapped['M'],mapped['D']).isoformat())
        except ValueError:continue
    candidates=sorted(set(candidates))
    return (candidates[0],'NORMALIZED',[]) if len(candidates)==1 else (None,'AMBIGUOUS_DATE',candidates) if candidates else (None,'INVALID_DATE',[])


def normalize(kind,value,settings=None):
    require(isinstance(kind,str) and kind in KINDS,'unsupported normalization kind');p=profile(settings)
    require(isinstance(value,(str,int,float,dict)) or value is None,'normalization value must be a bounded JSON scalar or address object')
    require(len(json.dumps(value,ensure_ascii=False,allow_nan=False))<=100000,'normalization input exceeds 100000 characters')
    raw=deepcopy(value);s=_space(str(value)) if value is not None else ''
    out={'schema':'loom.normalized.value.v1','normalizer_version':VERSION,'kind':kind,'raw':raw,
         'canonical':None,'state':'NORMALIZED','rule':'typed-'+kind+'/v1','profile':p,'profile_hash':digest(p),
         'warnings':[],'candidates':[],'assumptions':[],'identity_effect':'NONE'}
    if kind=='empty':
        require(value is None or value=='','empty requires null or empty string');out['state']='EMPTY'
    elif kind=='formula':out.update(canonical=str(value),state='UNEVALUATED_FORMULA')
    elif kind in ('text','name','business_name'):
        require(isinstance(value,str),'text normalization requires text')
        key=s.casefold()
        if kind=='business_name':key=re.sub(r'\bl\.?\s*l\.?\s*c\.?(?=\W|$)','llc',key).replace(',','');key=' '.join(key.split())
        out.update(canonical=key,display=s,search_key=' '.join(re.findall(r'\w+',unicodedata.normalize('NFKC',key))))
        if kind in ('name','business_name'):out['warnings'].append('NAME_FORM_ONLY_REVIEW_IDENTITY')
    elif kind=='identifier':
        require(isinstance(value,str),'identifiers require text to preserve leading zeros')
        if '\n' in value or '\r' in value:out['state']='OPEN_MULTILINE_IDENTIFIER';return out
        if p.get('identifier_separators'):
            out['canonical']=re.sub(r'[\s-]','',s);out['assumptions'].append('DECLARED_IDENTIFIER_SEPARATORS_IGNORED')
        else:out['canonical']=s
        if p.get('identifier_width'):
            if not re.fullmatch('[0-9]+',out['canonical']) or len(out['canonical'])>p['identifier_width']:
                out.update(state='OPEN_IDENTIFIER_WIDTH',canonical=None)
            else:
                out['canonical']=out['canonical'].zfill(p['identifier_width']);out['assumptions'].append('DECLARED_IDENTIFIER_ZERO_MASK')
        if re.search(r'[0-9]',s) and re.search(r'[OoIl]',s):out['warnings'].append('OCR_LOOKALIKE_REVIEW_OPEN')
        out['warnings'].append('NAMESPACE_AND_SCOPE_REQUIRED_FOR_IDENTITY')
    elif kind=='email':
        if not isinstance(value,str) or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',s):out['state']='INVALID_EMAIL'
        else:
            local,domain=s.rsplit('@',1)
            try:domain=domain.encode('idna').decode('ascii').lower()
            except UnicodeError:out['state']='INVALID_EMAIL_DOMAIN';return out
            if domain in p.get('email_casefold_domains',[]):
                local=local.casefold();out['assumptions'].append('DECLARED_DOMAIN_LOCAL_CASEFOLD')
            out['canonical']=local+'@'+domain
    elif kind=='phone':
        match=re.fullmatch(r'([+0-9().\s-]+?)(?:\s*(?:ext\.?|x|#)\s*([0-9]+))?',s,re.I)
        if not match:out['state']='INVALID_PHONE'
        else:
            main,ext=match.groups();digits=re.sub(r'\D','',main)
            if main.startswith('+') and 7<=len(digits)<=15 and digits[0]!='0':out['canonical']='+'+digits
            elif p.get('phone_country') and len(digits) in (10,11) and (len(digits)==10 or digits.startswith('1')):
                out['canonical']='+1'+(digits if len(digits)==10 else digits[1:]);out['assumptions'].append('DECLARED_NANP_COUNTRY')
            else:out.update(state='OPEN_PHONE_COUNTRY',candidates=[digits])
            if ext:out['extension']=ext
            if out['canonical'] and ext:out['canonical']+=';ext='+ext
    elif kind in ('date','datetime'):
        require(isinstance(value,str),'date normalization requires source text')
        if kind=='date':out['canonical'],out['state'],out['candidates']=_date(s,p)
        elif re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',s):
            out['canonical'],out['state'],out['candidates']=_date(s,p)
            if out['state']=='NORMALIZED':out.update(state='DATE_ONLY',precision='day')
        else:
            try:
                try:d=datetime.fromisoformat(s.replace('Z','+00:00'))
                except ValueError:d=parsedate_to_datetime(s)
                if d.tzinfo is None:out.update(state='OPEN_TIMEZONE',local_value=d.isoformat())
                else:out.update(canonical=d.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),offset_value=d.isoformat())
            except (ValueError,TypeError,IndexError):out['state']='INVALID_DATETIME'
        if p.get('date_order'):out['assumptions'].append('DECLARED_DATE_ORDER_'+p['date_order'])
        if p.get('two_digit_century') is not None:out['assumptions'].append('DECLARED_TWO_DIGIT_CENTURY')
    elif kind in ('number','money'):
        currency=p.get('currency');numeric=value
        if kind=='money' and isinstance(value,str):
            numeric=s
            codes=re.findall(r'\b(?:USD|CAD|AUD|EUR|GBP|JPY|KWD)\b',numeric)
            symbols=[c for c in ('€','£','¥','$') if c in numeric]
            if len(symbols)>1:out['state']='CURRENCY_CONFLICT';return out
            inferred=codes[0] if len(set(codes))==1 else None
            for symbol in symbols:
                candidate={'€':'EUR','£':'GBP','¥':None,'$':p.get('dollar_currency')}.get(symbol)
                if candidate and inferred and candidate!=inferred:out['state']='CURRENCY_CONFLICT';return out
                inferred=inferred or candidate
            if len(set(codes))>1 or currency and inferred and currency!=inferred:out['state']='CURRENCY_CONFLICT';return out
            currency=currency or inferred
            numeric=re.sub(r'\b(?:USD|CAD|AUD|EUR|GBP|JPY|KWD)\b','',numeric)
            numeric=re.sub(r'[$€£¥]','',numeric).strip()
            if symbols and not currency:out['warnings'].append('AMBIGUOUS_CURRENCY_SYMBOL')
        canonical,state=_number(numeric,p);out.update(canonical=canonical,state=state)
        if canonical is not None:
            out['exact_rational']=str(Fraction(canonical))
            if kind=='money':
                out['currency']=currency
                if currency:
                    if p.get('currency'):out['assumptions'].append('DECLARED_CURRENCY_'+currency)
                    scale=PRECISION[currency];minor=Fraction(canonical)*10**scale
                    if minor.denominator!=1:out['state']='OPEN_CURRENCY_PRECISION'
                    else:out['minor_units']=minor.numerator;out['minor_scale']=scale
                else:out['state']='OPEN_CURRENCY'
                out['warnings'].append('AMOUNT_ROLE_AND_MONEY_STAGE_REQUIRED')
    elif kind=='percentage':
        if p.get('percent_input')=='ratio':
            canonical,state=_number(value,p);out.update(state=state,unit='%')
            if canonical is not None:
                with localcontext() as ctx:
                    ctx.prec=512;percent=format(Decimal(canonical)*100,'f')
                out.update(canonical=percent.rstrip('0').rstrip('.') if '.' in percent else percent,
                           exact_ratio=str(Fraction(canonical)),assumptions=['DECLARED_PERCENT_RATIO_INPUT'])
        elif not isinstance(value,str) or not s.endswith('%'):out['state']='EXPLICIT_PERCENT_UNIT_REQUIRED'
        else:
            canonical,state=_number(s[:-1].strip(),p);out.update(canonical=canonical,state=state,unit='%')
            if canonical is not None:out['exact_ratio']=str(Fraction(canonical)/100)
    elif kind=='money_stage':
        key=s.casefold().rstrip('.')
        if key in STAGES:out['canonical']=STAGES[key]
        else:out.update(state='OPEN_STAGE_MEANING',candidates=['refunded','reimbursed','loan_repayment'] if key=='paid back' else [])
    elif kind=='address':
        if isinstance(value,dict):
            fields(value,('line1',),('unit','city','region','postal_code','country'))
            require(all(isinstance(v,str) for v in value.values()),'address components require text')
            out['canonical']={k:_space(v).casefold() for k,v in value.items()}
        else:
            require(isinstance(value,str),'address must be text or components')
            out.update(canonical=s.casefold(),state='ADDRESS_TEXT_REVIEW_OPEN')
        out['warnings'].append('UNIT_DIRECTION_AND_PARCEL_SCOPE_PRESERVED')
    return out


def value_packet(packet):
    fields(packet,('kind','value'),('profile',));return normalize(packet['kind'],packet['value'],packet.get('profile'))


def record(packet):
    fields(packet,('record_id','fields'),('profile','source_locator'))
    text(packet['record_id'],'record id');p=profile(packet.get('profile'));items=packet['fields']
    require(isinstance(items,list) and len(items)<=256,'at most 256 explicit normalization fields')
    rows=[];seen=set()
    for item in items:
        fields(item,('name','kind','value'),('path','scope','namespace','role','profile','source_span'))
        text(item['name'],'field name');key=(item['name'],json.dumps(item.get('path')))
        require(key not in seen,'duplicate normalization field coordinate');seen.add(key)
        r=normalize(item['kind'],item['value'],{**p,**profile(item.get('profile'))})
        rows.append({**{k:deepcopy(v) for k,v in item.items() if k not in ('value','profile','kind')},'value':r})
    return {'schema':'loom.normalized.record.v1','normalizer_version':VERSION,'record_id':packet['record_id'],
            'profile_hash':digest(p),'fields':rows,'source_locator':packet.get('source_locator'),
            'open_fields':[r['name'] for r in rows if r['value']['state'] not in ('NORMALIZED','EMPTY','UNEVALUATED_FORMULA')],
            'proof_effect':'NONE','identity_effect':'NONE'}


def derive(store,packet):
    fields(packet,('batch_id','expected_revision','records'),('profile',))
    snap=store.snapshot();require(snap['revision']==packet['expected_revision'],'normalization source revision changed')
    by={n['id']:n for n in snap['nodes']};nodes=[];items=packet['records']
    require(isinstance(items,list) and 0<len(items)<=100,'normalize 1..100 source records')
    for item in items:
        fields(item,('node_id','fields'))
        n=by.get(item['node_id']);require(n and n['type']!='source' and n['effective_status']!='stale' and n['source_ids'],'current source-linked record required')
        inputs=[]
        for selector in item['fields']:
            fields(selector,('name','kind'),('path','start','end','scope','namespace','role','profile'))
            if 'path' in selector:
                require('start' not in selector and 'end' not in selector,'choose path or text span')
                path=selector['path'];require(isinstance(path,list) and 1<=len(path)<=12 and path[0] in ('text','data','label'),'bounded text/data/label path required')
                v=n
                for part in path:
                    valid=isinstance(v,dict) and isinstance(part,str) and part in v or isinstance(v,list) and type(part) is int and 0<=part<len(v)
                    require(valid,'normalization field path unavailable');v=v[part]
            else:
                start=integer(selector.get('start'),0,len(n['text']),'normalization start');end=integer(selector.get('end'),start+1,len(n['text']),'normalization end');v=n['text'][start:end]
            entry={k:deepcopy(vv) for k,vv in selector.items() if k not in ('start','end')} | {'value':v}
            if 'path' not in selector:entry['source_span']={'start':start,'end':end,'quote':v}
            inputs.append(entry)
        view=record({'record_id':n['id'],'fields':inputs,'profile':packet.get('profile',{}),'source_locator':n.get('locator')})
        ident='normalized:'+digest([n['id'],n['content_hash'],view])[:32]
        if ident in by:continue
        nodes.append({'id':ident,'type':'analysis','space':'research','status':'derived','label':'Normalization · '+n['label'][:1800],
                      'source_ids':n['source_ids'],'depends_on':[{'kind':'node','id':n['id']}],
                      'data':{'source_node':n['id'],'source_content_hash':n['content_hash'],'normalization':view,'source_tier':'normalization_view'}})
    if not nodes:return result('IDEMPOTENT',normalized=0)
    receipt=store.apply({'schema':'loom.graph.batch.v1','batch_id':packet['batch_id'],'nodes':nodes},expected_revision=snap['revision'])
    return result('NORMALIZED_VIEWS',normalized=len(nodes),node_ids=[n['id'] for n in nodes],commit=receipt)


def inspect(store,node_id):
    rows=[n for n in store.snapshot(include_stale=True)['nodes'] if n['data'].get('source_node')==node_id and n['data'].get('normalization')]
    return result('NORMALIZATION_VIEWS',source_node=node_id,views=rows)


def call(name,values):
    require(len(values)==1,'normalization function takes one packet')
    return {'normalize_value':value_packet,'normalize_record':record}[name](read_json(values[0]))


def metadata(meta,settings=None):
    """Nonblank cells get typed views; source headers retain their financial role."""
    rows=[];headers=meta.get('column_headers',{})
    for cell,value in meta.get('cells',{}).items():
        if value=='':continue
        header=headers.get(re.sub(r'[0-9]+$','',cell),'') if meta.get('sheet') else cell
        if meta.get('duplicate_headers') and cell.startswith('column:'):
            header=meta['columns'][int(cell.split(':')[1])-1]['header']
        key=_space(header).casefold()
        kind='formula' if value.startswith('=') else 'text'
        heading=meta.get('header_row') is not None and meta.get('row',meta['header_row'])<=meta['header_row']
        if kind!='formula' and not heading:
            if re.search(r'\b(loan|account|check|invoice|parcel)\b.*\b(number|no|id|#)\b',key) or key in ('loan','account','check','invoice','parcel','id','code','key','number','item no.','item no'):kind='identifier'
            elif re.search(r'\bdate\b',key) and not re.search(r'\bto\s+date\b',key):kind='datetime' if meta.get('cell_types',{}).get(cell)=='d' else 'date'
            elif 'email' in key:kind='email'
            elif 'phone' in key:kind='phone'
            elif 'name' in key:kind='name'
            elif value.strip().endswith('%'):kind='percentage'
            elif re.search(r'\b(amount|budget|cost|credit|deposit|paid|balance|value)\b',key) and re.fullmatch(r'[\s$€£¥0-9.,()+-]+',re.sub(r'\b(?:USD|CAD|AUD|EUR|GBP|JPY|KWD)\b','',value)):kind='money'
            elif meta.get('cell_types',{}).get(cell)=='n' and re.fullmatch(r'[0-9.,()+-]+',value):kind='number'
        fmt=meta.get('number_formats',{}).get(cell,'')
        percent_format='%' in re.sub(r'"[^"]*"|\\.','',fmt)
        if kind!='formula' and not heading and meta.get('cell_types',{}).get(cell)=='n' and percent_format:kind='percentage'
        field={'name':cell,'kind':kind,'value':value,'path':['data','cells',cell],
               'role':header or 'unlabeled cell; role awaiting mapping'}
        if percent_format and kind=='percentage' and not value.strip().endswith('%'):field['profile']={'percent_input':'ratio'}
        if kind=='identifier' and meta.get('cell_types',{}).get(cell)=='n' and re.fullmatch('0{1,64}',fmt):field['profile']={'identifier_width':len(fmt)}
        rows.append(field)
    return record({'record_id':'intake-section','fields':rows[:256],'profile':settings or {}})


def cli(args):
    parser=argparse.ArgumentParser(prog='loom normalize');subs=parser.add_subparsers(dest='command',required=True)
    for name in ('value','record','graph','inspect'):
        p=subs.add_parser(name);p.add_argument('input');p.add_argument('--out')
        if name in ('graph','inspect'):p.add_argument('--db',required=True)
    a=parser.parse_args(args)
    try:
        if a.command in ('graph','inspect'):
            from .store import GraphStore
            s=GraphStore(a.db);out=inspect(s,a.input) if a.command=='inspect' else derive(s,read_json(a.input))
        else:out={'value':value_packet,'record':record}[a.command](read_json(a.input))
        raw=json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
        if a.out:
            with Path(a.out).open('x') as f:f.write(raw)
        else:print(raw,end='')
        return 0
    except (ValueError,TypeError,KeyError,OSError) as e:
        print(json.dumps(result('ERROR',diagnostic=str(e))));return 2
