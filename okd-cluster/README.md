# Gestione cluster ZMenu Next

Tutti i comandi shell riportati sotto si lanciano dalla directory zmenu_manager_cluster

## Creazione cluster

oc apply -f .\02_cluster-app\

## Cancellazione cluster

oc delete -f .\02_cluster-app\

## Caricamento configmap con il file di configurazione di nginx

### Per FE backoffice

oc create configmap fe-nginx-conf --from-file=nginx_conf\nginx_fe.conf

### Per FE Manager

oc create configmap namager-fe-nginx-conf --from-file=nginx_conf\nginx_managerfe.conf

### Per connettersi al db

Si crea un link al servizio in locale e si accede in localhost sulla porta opportuna.

oc port-forward svc/postgres 5433:5432
