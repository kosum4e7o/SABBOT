STREAM ACTIVITY BOT v20.2

КАК СЕ ВЗЕМА ИНСТАЛАТОРЪТ (GitHub Actions)
  1. Качи ЦЯЛОТО съдържание на тази папка в GitHub repository (включително .github/workflows).
  2. GitHub -> Actions -> "Build Windows Installer" -> Run workflow.
  3. След зелен build: Artifacts -> Stream_Activity_Bot_Installer -> свали.
     В него има САМО един файл: Stream_Activity_Bot_Setup.exe
     (GitHub винаги опакова сваления artifact в .zip - разархивирай го; вътре е само този EXE.
      За директно сваляне на .exe: създай tag "v20.2" - EXE-то се прикачва към GitHub Release.)
  4. Двоен клик на Stream_Activity_Bot_Setup.exe -> Next -> Install -> Finish.
     Не са нужни Python, PySide6, DLL, нищо друго. Деинсталиране: Windows Settings -> Apps.

ТАЙМЕР (countdown) - ТОЧНО ПРАВИЛО
  Моето чат съобщение  = RESET        Моето !points = RESET        Моето !time = RESET
  Успешно автоматично съобщение на програмата = RESET (неуспешно = без reset)
  Отговор на BOTTLY    = БЕЗ reset (записва се в "Последни действия", таймерът не се мести)

ДИАГНОСТИКА
  Лог файл: %APPDATA%\Stream Activity Bot\stream_activity_bot.log
  Редове: CHAT RECEIVED, ACCOUNT MATCH, PENDING COMMAND CREATED, BOTTLY RESPONSE, POINTS PARSED,
          WATCH TIME PARSED, TIMER RESET REASON=..., TIMER NOT RESET REASON=BOTTLY_RESPONSE,
          WEBSOCKET CONNECTED, PUSHER HANDSHAKE, SUBSCRIPTION SENT/SUCCEEDED, CHAT EVENT PARSED.
  Токени и пароли никога не се логват.

ТЕСТОВЕ (локално):  python -m unittest discover -s tests -t . -v

v20.0 - ПОПРАВКИ
  * Корен на проблема: Engine.handle_incoming_chat_message() не приемаше параметъра reply_to, който
    Kick readers винаги подава -> TypeError за ВСЯКО съобщение преди activity/timer/!points/!time.
    (Чатът се виждаше, защото live feed-ът се пълни преди грешката.)
  * kick_events.py: централен normalize_chat_message() + match_account() (user_id, username, slug, alias).
  * Един pipeline Engine.process_chat_message(); отделни record_user_chat_activity(),
    reset_activity_timer(), handle_bot_response(), send_automatic_message().
  * BOTTLY отговор никога не пипа таймера; чужд отговор не се записва на моя акаунт;
    чужд viewer не може да зададе моите точки; моето собствено "@аз има N точки" не се третира като бот.
  * parse_points_response() / parse_watch_time_response(); pending команди с TTL.
  * Account card: без Level и без "Последно"; без измислени 0 стойности. Stream Chat със стил.
  * Реален Windows installer (Inno Setup) вместо самописния PySide6 installer; CI smoke test.
