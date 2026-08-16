# Every check this project has lives in one script, so CI, the pre-commit hook and
# your laptop run the same thing. A check that exists only in the workflow file is a
# check you discover late.

.DEFAULT_GOAL := check

.PHONY: check check-all fast hooks

# The default variant, end to end: render, assert, then install and exercise both
# halves of the generated project.
check:
	./devtools/check_template.sh default

# Every license variant, as CI does.
check-all:
	./devtools/check_template.sh default
	./devtools/check_template.sh proprietary
	./devtools/check_template.sh no-license

# Render and assert only, skipping the installs. Seconds rather than minutes, and it
# cannot catch anything that only shows up once the code runs.
fast:
	FAST=1 ./devtools/check_template.sh default

hooks:
	@sh devtools/install-hooks.sh
