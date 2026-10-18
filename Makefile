.DEFAULT_GOAL := check

.PHONY: check check-all fast render hooks screenshots

check:
	./devtools/check_template.sh default

check-all:
	./devtools/check_template.sh default
	./devtools/check_template.sh proprietary
	./devtools/check_template.sh no-license

fast:
	FAST=1 ./devtools/check_template.sh default

render:
	@./devtools/render.sh

hooks:
	@sh devtools/install-hooks.sh

screenshots:
	@sh devtools/screenshots.sh
