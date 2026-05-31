all: localecompile
LNGS:=$(shell for d in teamshifts/locale/*; do [ -d "$$d" ] && printf '%s ' "-l $${d##*/}"; done)

localecompile:
	django-admin compilemessages

localegen:
	django-admin makemessages --keep-pot -i build -i dist -i "*egg*" $(LNGS)

.PHONY: all localecompile localegen
