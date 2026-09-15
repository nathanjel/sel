from run import call, HERE

def main():
    ip=lambda name:call(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',name]).strip()
    cmd=['docker','exec','-e','AUDIT_PG='+ip('sel-audit-pg-0915'),'-e','AUDIT_MARIA='+ip('sel-audit-maria-0915'),'sel-audit-tools-0915','php','/work/tools/adversarial/scale-db.php']
    result=call(cmd)
    (HERE/'scale-db-results.json').write_text(result)
    print(result)

if __name__=='__main__':main()
