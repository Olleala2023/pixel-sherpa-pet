@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка Шерпы ===
echo.

rem 1. Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Не найден Python. Установите Python 3.11 или новее с https://www.python.org/downloads/
    echo и поставьте галочку "Add python.exe to PATH". Потом запустите этот файл ещё раз.
    goto :fail
)

rem 2. Виртуальное окружение .venv — зависимости ставятся в него, а не в систему
if not exist ".venv\Scripts\python.exe" (
    echo Создаю окружение .venv ...
    %PY% -m venv .venv || goto :fail
)

rem 3. Пакеты
echo Ставлю пакеты из requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

rem 4. Модель распознавания речи - по желанию (около 225 МБ)
echo.
if exist "brain\models\gigaam-v3-e2e\model.int8.onnx" (
    echo Модель распознавания речи уже установлена.
    goto :modeldone
)
echo Локальное распознавание речи (чтобы говорить с Шерпой голосом).
echo   Модель:  GigaAM v3 e2e - русский язык, с заглавными буквами и знаками препинания
echo   Размер:  около 225 МБ, скачивается с Hugging Face
echo   Работает на вашем компьютере, без интернета; аудио никуда не отправляется.
echo.
echo Установить эту модель?   1 - да, установить    2 - нет, пропустить
choice /c 12 /n >nul
if errorlevel 2 goto :skipmodel
echo.
echo Скачиваю модель GigaAM v3 e2e ...
".venv\Scripts\python.exe" tools\download_model.py || goto :fail
goto :modeldone

:skipmodel
echo.
echo Модель пропущена. Питомец будет работать, но голосом спросить не получится.
echo Установить её позже:  .venv\Scripts\python.exe tools\download_model.py

:modeldone
echo.
echo Готово. Запускайте питомца файлом Запустить_Шерпу.bat.
echo Ключ OpenRouter введите в окне "Настройки" (правый щелчок по псу).
pause
exit /b 0

:fail
echo.
echo Установка не завершена, причина выше.
pause
exit /b 1
