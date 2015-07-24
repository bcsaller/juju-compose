FROM python:2-onbuild
ENV PYTHONPATH=$PYTHONPATH:/usr/src/app/
CMD [ "python", "./juju_compose/__init__.py"]
