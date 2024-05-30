docker run --name local_postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=admin -p 5432:5432 -d pgvector/pgvector:pg16
#docker run --name local_pgadmin -p 5050:80 -e 'PGADMIN_DEFAULT_EMAIL=your_email@.com' -e 'PGADMIN_DEFAULT_PASSWORD=postgres' -d dpage/pgadmin4
