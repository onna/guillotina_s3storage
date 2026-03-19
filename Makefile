s3:
	docker run -d -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address ":9001" -name 
	docker run -d -e "SERVICES=s3" -p 4566:4566 localstack/localstack

install:
	pip install -e .[test]
	pip install setuptools

pre-checks-deps: lint-deps
	pip install flake8 "mypy_zope>=1.0,<2" "mypy>=1.8,<2"

pre-checks: pre-checks-deps
	flake8 guillotina_s3storage --config=setup.cfg
	black --check --verbose guillotina_s3storage
	mypy -p guillotina_s3storage --ignore-missing-imports

lint-deps:
	pip install "isort>=5" black

lint:
	isort -rc guillotina_s3storage
	black guillotina_s3storage


tests: install
	# Run tests
	pytest --capture=no --tb=native -v guillotina_s3storage
