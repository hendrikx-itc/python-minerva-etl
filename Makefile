#!/usr/bin/make

all:


clean:


install:
	install -d $(DESTDIR)/usr/share/minerva/
	install -m 0644 *.sql $(DESTDIR)/usr/share/minerva/
	install -d $(DESTDIR)/usr/share/minerva/public/
	install -m 0644 public/*.sql $(DESTDIR)/usr/share/minerva/public/
	install -d $(DESTDIR)/usr/share/minerva/directory/
	install -m 0644 directory/*.sql $(DESTDIR)/usr/share/minerva/directory/
	install -d $(DESTDIR)/usr/share/minerva/system/
	install -m 0644 system/*.sql $(DESTDIR)/usr/share/minerva/system/
	install -d $(DESTDIR)/usr/share/minerva/relation/
	install -m 0644 relation/*.sql $(DESTDIR)/usr/share/minerva/relation/
	install -d $(DESTDIR)/usr/share/minerva/extensions/
	install -d $(DESTDIR)/usr/bin/
	install -m 0755 init-minerva-db $(DESTDIR)/usr/bin/
	install -d $(DESTDIR)/etc/
	install -m 0600 minerva_db.conf $(DESTDIR)/etc/
