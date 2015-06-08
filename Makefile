test: clean reset
	tox

reset:
	@echo Resetting for manual testing
	@rm -rf tests/trusty/tester
	@git checkout tests/trusty/tester

clean:
	@rm -rf out
