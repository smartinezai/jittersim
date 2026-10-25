import csv,io,json,sys
from jittersim.connection_check import check_export_csv
pairs=[('\r1',"'\r1"),('before\rafter','before\rafter'),('before\r\nafter','before\r\nafter')]
for prefix in ['=','+','-','@','\t','\r']:
 pairs.append((prefix+'1',"'"+prefix+'1'))
for label,expected in pairs:
 rows=list(csv.DictReader(io.StringIO(check_export_csv([{'label':label}]),newline='')))
 assert len(rows)==1 and rows[0]['label']==expected and rows[0]['profile_name']==''
print(json.dumps({'python':sys.version,'source':__import__('jittersim.connection_check',fromlist=['']).__file__,'cases':len(pairs),'result':'PASS'}))
