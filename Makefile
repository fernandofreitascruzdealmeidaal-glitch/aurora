# Atalhos. No Windows sem "make", copie o comando da linha correspondente.

.PHONY: instalar testar verificar formatar servir firmware-testar firmware-gravar

instalar:            ## instala o servidor e as ferramentas de teste
	pip install -e "servidor[dev]"

testar:              ## roda todos os testes do servidor com cobertura
	cd servidor && pytest --cov

verificar: testar firmware-testar  ## tudo o que a integração contínua roda
	ruff check .
	ruff format --check .

formatar:            ## arruma o estilo do código Python automaticamente
	ruff format .
	ruff check --fix .

servir:              ## inicia o servidor e o painel em http://127.0.0.1:8000
	aurora servir

firmware-testar:     ## testa a lógica do firmware no computador (precisa de g++)
	./firmware/teste_nativo/rodar.sh

firmware-gravar:     ## compila e grava no ESP32 conectado por USB
	cd firmware && pio run -e esp32dev -t upload && pio device monitor
