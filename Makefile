.PHONY: docs

docs:
	$(MAKE) -C docs clean html SPHINXOPTS='-vv'