FROM python:2-onbuild
ENV PYTHONPATH=$PYTHONPATH:/usr/src/app/
ENTRYPOINT [ "python", "./juju_compose/__init__.py"]
