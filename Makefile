.DEFAULT_GOAL := help


## bootstrap
bootstrap:
	npx cdk bootstrap

test:
	mypy \
		--ignore-missing-imports \
		--strict-optional \
		--disallow-untyped-defs \
		--disallow-untyped-calls \
		./function/src/*.py

## build
build:
	-rm -rf ./function/.artifact/
	rsync -a ./function/src/ ./function/.artifact/ \
		--exclude /.mypy_cache/ \
		--exclude /__pycache__/
	pip install -r ./function/.artifact/requirements.txt -t ./function/.artifact/

## synth
synth:
	npx cdk synth

## diff
diff:
	-npx cdk diff

## deploy
deploy:
	npx cdk deploy '*' --require-approval never

## destroy
force_destroy:
	npx cdk destroy --force

## help
help:
	@make2help $(MAKEFILE_LIST)


.PHONY: help
.SILENT:
