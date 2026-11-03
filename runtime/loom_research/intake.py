"""Explicit local document intake; bounded batches, immutable native fingerprints.

Email headers and each MIME text part stay separate. Spreadsheet rows retain
column names and formulas. PDF extraction emits OCR-required flags for empty
pages; an installed OCR tool can supply a separately labeled faithful extract.
"""
import csv
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import hashlib
import io
import re
import zipfile
import posixpath
from xml.etree import ElementTree as ET
from pathlib import Path
from .contracts import fields, require, result, digest, text
from .engine import records_batch


def xlsx_layout(raw):
    """Read native merged ranges without filling their blank cells or evaluating formulas."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        require(len(infos)<=4096 and sum(i.file_size for i in infos)<=64*1024**2,'workbook ZIP expansion budget exceeded')
        def xml(name):
            content=archive.read(name)
            require(b'<!DOCTYPE' not in content.upper(),'worksheet XML document types are unsupported')
            return ET.fromstring(content)
        ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        links={n.attrib['Id']:n.attrib for n in xml('xl/_rels/workbook.xml.rels')}
        layout={}
        for sheet in xml('xl/workbook.xml').find(ns+'sheets'):
            rel=links[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            require(rel.get('TargetMode')!='External','external worksheet target unsupported')
            target=rel['Target'];name=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
            require(name.startswith('xl/') and '..' not in name.split('/'),'worksheet target outside workbook')
            tree=xml(name)
            layout[sheet.attrib['name']]={'merged_ranges':[m.attrib['ref'] for m in tree.findall(ns+'mergeCells/'+ns+'mergeCell')],
                                         'hidden_rows':[int(n.attrib['r']) for n in tree.findall(ns+'sheetData/'+ns+'row') if n.attrib.get('hidden')=='1']}
        return layout


def sections(path,raw,options=None):
    options=options or {}
    suffix=path.suffix.lower();warnings=[]
    if suffix in ('.eml','.emlx'):
        if suffix=='.emlx':
            line,sep,body=raw.partition(b'\n');require(sep and line.isdigit(),'invalid emlx length header')
            raw=body[:int(line)];require(len(raw)==int(line),'incomplete emlx body')
        msg=BytesParser(policy=policy.default).parsebytes(raw)
        headers={k:msg.get_all(k,[]) for k in dict.fromkeys(msg.keys())}
        # Keep export/reconstruction, Received and envelope qualifications visible.
        # Body-quoted sender/date fields remain reported text inside this outer message.
        clocks={}
        try:
            dt=parsedate_to_datetime(str(msg.get('Date','')))
            if dt.tzinfo:clocks={'statement':[dt.isoformat(),dt.isoformat()]}
            else:warnings.append('HEADER_DATE_ZONE_UNKNOWN')
        except (ValueError,TypeError,IndexError):warnings.append('HEADER_DATE_UNKNOWN')
        attachments=[]
        for number,p in enumerate(msg.walk()):
            if not (p.get_filename() or p.get_content_disposition()=='attachment'):continue
            payload=p.get_payload(decode=True) or b''
            require(len(payload)<=16*1024**2,'attachment byte budget exceeded')
            detected='opaque'
            if payload.startswith(b'PK'):
                with zipfile.ZipFile(io.BytesIO(payload)) as z:
                    infos=z.infolist()
                    require(len(infos)<=4096 and sum(i.file_size for i in infos)<=64*1024**2,'attachment ZIP expansion budget exceeded')
                    if 'xl/workbook.xml' in z.namelist():detected='xlsx'
            elif payload.startswith(b'%PDF'):detected='pdf'
            attachments.append({'name':p.get_filename(),'content_type':p.get_content_type(),
                                'mime_part':number,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
                                'detected_format':detected})
        out=[]
        for i,part in enumerate(msg.walk()):
            if part.get_content_type()!='text/plain' or part.get_content_disposition()=='attachment' or part.get_filename():continue
            body=part.get_content();require(isinstance(body,str),'email body decoder must return text')
            out.append((f'MIME part {i}',body,{'headers':headers,'attachments':attachments,'medium':'email','clocks':clocks,
                                           'reconstruction_note':str(msg.get('X-Reconstruction-Note','')) or None,
                                           'receipt_state':'OPEN_ACTOR_RECEIPT','speech_scope':'whole MIME part; quoted text may contain other speakers'}))
        if not out:warnings.append('PLAIN_TEXT_BODY_UNAVAILABLE')
        if options.get('structured_email',False):
            require(type(options['structured_email']) is bool,'structured_email must be boolean')
            envelope='\n'.join(k+': '+str(v) for k in ('From','To','Cc','Date','Subject','Message-ID','X-Reconstruction-Note')
                               for v in msg.get_all(k,[]))
            if envelope:out.append(('Decoded envelope headers',envelope,{'medium':'email_envelope','headers':headers,
                                  'clocks':clocks,'receipt_state':'OPEN_ACTOR_RECEIPT',
                                  'reconstruction_note':str(msg.get('X-Reconstruction-Note','')) or None}))
        if options.get('attachments',False):
            require(type(options['attachments']) is bool,'attachments must be boolean')
            for a in attachments:
                payload=list(msg.walk())[a['mime_part']].get_payload(decode=True) or b''
                base={'medium':'attachment','attachment_sha256':a['sha256'],'attachment_name':a['name'],
                      'attachment_mime_part':a['mime_part'],'declared_content_type':a['content_type'],
                      'detected_format':a['detected_format'],'headers':headers,
                      'clocks':clocks,'receipt_state':'OPEN_ACTOR_RECEIPT'}
                out.append((f'Attachment part {a["mime_part"]} fingerprint',
                            f'Attachment: {a["name"]}\nSHA256: {a["sha256"]}\nFormat: {a["detected_format"]}',base))
                if a['detected_format']=='xlsx':
                    rows,flags=sections(Path('attachment.xlsx'),payload,{'sheets':options.get('attachment_sheets',{})})
                    warnings.extend(flags)
                    for loc,body,meta in rows:
                        out.append((f'Attachment part {a["mime_part"]} :: {loc}',body,{**base,**meta}))
                else:warnings.append('ATTACHMENT_CONTENT_OPEN:part:'+str(a['mime_part']))
        return out,warnings
    if suffix=='.pdf':
        from pypdf import PdfReader
        out=[]
        # Passing a path makes pypdf copy the whole file into BytesIO. Passing
        # an open binary handle keeps page selection seekable without that copy.
        with (path.open('rb') if raw is None else io.BytesIO(raw)) as stream:
            reader=PdfReader(stream)
            require(len(reader.pages)<=500 or bool(options.get('pages')),'large PDFs require explicitly selected pages')
            pages=options.get('pages',list(range(1,len(reader.pages)+1)))
            require(isinstance(pages,list) and pages and len(pages)<=500 and all(type(i) is int and 1<=i<=len(reader.pages) for i in pages),'invalid explicit PDF pages')
            for i in pages:
                page=reader.pages[i-1]
                body=page.extract_text() or ''
                quality='TEXT_EXTRACTED' if sum(ch.isalnum() for ch in body)>=80 else 'SPARSE_TEXT_REVIEW_REQUIRED' if body.strip() else 'OCR_REQUIRED'
                if quality!='TEXT_EXTRACTED':warnings.append(quality+':page:'+str(i))
                out.append((f'PDF page {i}',body,{'page':i,'extraction_quality':quality,'medium':'document'}))
        return out,warnings
    if suffix=='.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        layout=xlsx_layout(raw)
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False);out=[]
        try:
            for sheet in book:
                selections=options.get('sheets',{})
                require(isinstance(selections,dict) and all(k in book.sheetnames for k in selections),'unknown selected worksheet')
                if selections and sheet.title not in selections:continue
                selection=selections.get(sheet.title,{})
                fields(selection,(),('header_row','start_row','end_row'))
                first=selection.get('start_row',1);last=selection.get('end_row',min(sheet.max_row,20000));header=selection.get('header_row')
                require(type(first) is int and type(last) is int and 1<=first<=last<=20000,'worksheet range must be 1..20000')
                require(header is None or type(header) is int and 1<=header<=20000,'header_row must be explicit and in 1..20000')
                headings=list(next(sheet.iter_rows(min_row=header,max_row=header,values_only=True))) if header else []
                for number,objects in enumerate(sheet.iter_rows(values_only=False),1):
                    require(number<=20000,'spreadsheet row budget is 20000 per sheet')
                    if number<first:continue
                    if number>last:break
                    row=[c.value for c in objects]
                    if not any(v is not None for v in row):continue
                    require(len(row)<=256,'spreadsheet column budget is 256')
                    cells={get_column_letter(i+1)+str(number):str(v) if v is not None else '' for i,v in enumerate(row)}
                    labels={get_column_letter(i+1):str(v) if v is not None else '' for i,v in enumerate(headings)}
                    body='\n'.join(cell+(' ['+labels.get(re.sub(r'\d+$','',cell),'')+']' if labels.get(re.sub(r'\d+$','',cell)) and number>header else '')+': '+(v if v else '(blank)') for cell,v in cells.items())
                    types={get_column_letter(i+1)+str(number):c.data_type for i,c in enumerate(objects) if c.value is not None}
                    formats={get_column_letter(i+1)+str(number):c.number_format for i,c in enumerate(objects) if c.value is not None}
                    out.append((f'{sheet.title}!row {number}',body,{'sheet':sheet.title,'row':number,'cells':cells,
                        'cell_types':types,'number_formats':formats,'layout':layout[sheet.title],
                        'column_headers':labels,'header_row':header,'formulas':'preserved; unevaluated','medium':'spreadsheet'}))
                    require(len(out)<=1000,'intake passage budget is 1000; select a smaller workbook')
        finally:book.close()
        return out,warnings
    body=raw.decode('utf-8-sig',errors='strict')
    if suffix=='.csv':
        reader=csv.reader(io.StringIO(body));headers=next(reader,None)
        require(headers and len(headers)<=256,'CSV requires 1..256 headers')
        duplicates=len(headers)!=len(set(headers));retain=options.get('retain_duplicate_headers',False)
        require(type(retain) is bool,'retain_duplicate_headers must be boolean')
        require(not duplicates or retain,'CSV requires distinct headers; use retain_duplicate_headers for positional preservation')
        out=[]
        for number,values in enumerate(reader,2):
            if not values:continue
            require(len(out)<1000 and len(values)==len(headers),'CSV exceeds passage budget or has missing/extra cells')
            row={('column:'+str(i+1) if duplicates else k):v for i,(k,v) in enumerate(zip(headers,values))}
            out.append((f'CSV row {number}','\n'.join(k+': '+v for k,v in zip(headers,values)),
                        {'row':number,'cells':row,'raw_headers':headers,'duplicate_headers':duplicates,
                         'columns':[{'position':i+1,'header':k,'raw':v} for i,(k,v) in enumerate(zip(headers,values))],
                         'medium':'spreadsheet'}))
        return out,warnings
    require(suffix in ('.txt','.md','.json','.jsonl'), 'supported formats: eml/emlx/pdf/xlsx/csv/txt/md/json/jsonl')
    return [('text',body,{'medium':'text','interpretation':'faithful decoded text; source role supplied by operator'})],warnings


def prepare(packet):
    fields(packet,('batch_id','files'),('chunk_chars',))
    files=packet['files'];require(isinstance(files,list) and 0<len(files)<=32,'select 1..32 explicit files')
    size=packet.get('chunk_chars',6000);require(type(size) is int and 1000<=size<=20000,'chunk_chars must be 1000..20000')
    records=[];manifest=[];total=0
    for item in files:
        fields(item,('path',),('label','source_tier','data','pages','sheets','attachments','attachment_sheets','structured_email','normalization_profile','retain_duplicate_headers'))
        path=Path(text(item['path'],'path',8000)).expanduser().resolve();require(path.is_file(),'selected file unavailable')
        before=path.stat();large_pdf=path.suffix.lower()=='.pdf' and bool(item.get('pages'))
        require(before.st_size<=(512 if large_pdf else 16)*1024**2,'document exceeds intake byte budget')
        if large_pdf:require(len(item['pages'])<=64,'large PDF selection is limited to 64 pages')
        total+=before.st_size;require(total<=1024*1024**2,'batch source fingerprint budget exceeds 1 GiB')
        if large_pdf:
            h=hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
            sha=h.hexdigest();raw=None
        else:
            raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        parts,warnings=sections(path,raw,item);after=path.stat()
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'native file changed during extraction; retry from stable bytes')
        manifest.append({'path':str(path),'native_sha256':sha,'bytes':before.st_size,'warnings':warnings,'sections':len(parts)})
        for locator,body,meta in parts:
            if meta.get('cells'):
                from .normalization import metadata
                meta={**meta,'normalization':metadata(meta,item.get('normalization_profile'))}
            for start in range(0,max(1,len(body)),size):
                end=min(len(body),start+size);chunk=body[start:end]
                ident='doc:'+digest([str(path),sha,locator,start,end])[:32]
                data={**item.get('data',{}),**meta,'native_path':str(path),'native_sha256':sha,
                      'native_bytes_verified':True,'source_tier':item.get('source_tier','operator_supplied'),
                      'extractor':'loom.document-intake.v1','span':{'section':locator,'start':start,'end':end},
                      'context_split':start>0 or end<len(body),'native_context_required':True}
                clocks=data.pop('clocks',{})
                records.append({'id':ident,'origin':str(path),'locator':f'{path} :: {locator} :: chars {start}:{end}',
                                'label':item.get('label',path.name)+' · '+locator,'text':chunk,'native_sha256':sha,'data':data,'clocks':clocks})
                require(len(records)<=1000,'batch exceeds 1000 passages; select fewer files or larger chunks')
    return records,manifest


def ingest(store,packet):
    records,manifest=prepare(packet);snap=store.snapshot(include_stale=True,include_inactive=True)
    from .contracts import normalize_node
    old={n['id']:n for n in snap['nodes']};fresh=[]
    for r in records:
        prior=old.get(r['id'])
        intended=normalize_node(records_batch('compare',[r])['nodes'][1])
        if prior is None or not prior['active'] or any(prior.get(k)!=intended.get(k) for k in ('text','data','clocks','identifiers')):
            fresh.append(r)
    for record in fresh:
        if record['id'] in old:record['revision']=max(old[record['id']]['revision'],old['src:'+record['id']]['revision'])+1
    if not fresh:return result('IDEMPOTENT',imported=0,duplicates=len(records),manifest=manifest)
    batch=records_batch(packet['batch_id'],fresh);batch['edges']=[]
    for n in batch['nodes']:
        if n['type']=='source':
            n['data']['native_bytes_verified']=True
            n['data']['extractor']='loom.document-intake.v1'
            parents=[o for o in snap['nodes'] if o['type']=='source' and o['active'] and o.get('origin')==n['origin']
                     and o.get('data',{}).get('native_sha256_supplied')!=n['data']['native_sha256_supplied']]
            # File version links remain documentary lineage, with bounded preview.
            for parent in parents[:32]:
                batch['edges'].append({'id':'version:'+digest([n['id'],parent['id']])[:32], 'subject':n['id'],
                                       'predicate':'version_of','object':parent['id'],'status':'derived','source_ids':[n['id']],
                                       'data':{'meaning':'different bytes at same explicitly selected path'}})
    receipt=store.apply(batch,expected_revision=snap['revision'])
    return result('IMPORTED',imported=len(fresh),duplicates=len(records)-len(fresh),manifest=manifest,commit=receipt,
                  boundary='Native hashes verify the selected bytes. Extracted passages retain context and interpretation review requirements; receipt clocks stay open.')
