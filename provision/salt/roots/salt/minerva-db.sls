postgresql:
  pkg:
    - installed
  service:
    - running

postgis:
  pkg:
    - installed

postgresql-9.3-postgis-scripts:
  pkg:
    - installed

postgresql-server-dev-9.3:
  pkg:
    - installed

libpq-dev:
  pkg:
    - installed

python-virtualenv:
  pkg:
    - installed

language-pack-nl:
  pkg:
    - installed

python-package:
  pip.installed:
    - editable: /vagrant/
    - requires:
      - pkg: python-psycopg2

# Psycopg2 requires compilation, so it is easier to use the standard Ubuntu
# package
python-psycopg2:
  pkg:
    - installed

# python_dateutil from pypi currently has permission issues with some files
# after installation, so use the standard Ubuntu package
python-dateutil:
  pkg:
    - installed

vagrant:
  user.present:
    - shell: /bin/zsh

  postgres_user.present:
    - login: True
    - superuser: True
    - require:
      - service: postgresql

minerva:
  postgres_database:
    - present

init-minerva-db:
  cmd.wait:
    - name: '/vagrant/schema/run-scripts /vagrant/schema/scripts'
    - user: vagrant
    - env:
      - PGDATABASE: minerva
    - watch:
      - postgres_database: minerva

git:
  pkg:
    - installed

install-pgtap:
  cmd.wait:
    - name: '/vagrant/provision/install_pgtap'
    - user: vagrant
    - watch:
      - cmd: init-minerva-db
    - require:
      - pkg: git

/etc/minerva/instances/default.conf:
  file.managed:
    - source: salt://resources/minerva_instance.conf
    - makedirs: True

/etc/postgresql/9.3/main/postgresql.conf:
  file.append:
    - text: 'minerva.trigger_mark_modified = on'