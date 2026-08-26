# Every check this project has lives in one script, so the pre-commit hook and your
# laptop run the same thing. Rendering is devtools/render.sh, which the check script
# calls and `make render` exposes on its own.

.DEFAULT_GOAL := check

.PHONY: check check-all fast render hooks

# The default variant, end to end: render, assert, then install and exercise both
# halves of the generated project.
check:
	./devtools/check_template.sh default

# Every license variant.
check-all:
	./devtools/check_template.sh default
	./devtools/check_template.sh proprietary
	./devtools/check_template.sh no-license

# Render and assert only, skipping the installs. Seconds rather than minutes, and it
# cannot catch anything that only shows up once the code runs.
fast:
	FAST=1 ./devtools/check_template.sh default

# Render the template and print where it landed, asserting nothing. For working on a
# single check: render once, then run that check against the path as many times as
# the change takes, instead of paying a full run per edit. The directory is yours to
# remove.
render:
	@./devtools/render.sh

hooks:
	@sh devtools/install-hooks.sh
