/* Kuskovatost.exe — запуск переносной версии одним щелчком.
 *
 * Запускает runtime\pythonw.exe -E -s app\run.py [файлы...] из папки, где лежит
 * сам exe (пути с кириллицей и пробелами — через Unicode-API). -E и -s:
 * не смотреть на переменные PYTHON* и пользовательские пакеты — установленный
 * у заказчика Python (если есть) на программу не влияет.
 * При первом запуске предлагает создать ярлык на рабочем столе.
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define COBJMACROS
#include <windows.h>
#include <shlobj.h>
#include <objbase.h>
#include <shobjidl.h>
#include <wchar.h>

#define TITLE L"Кусковатость породы по фото"
#define LINK_NAME L"\\Кусковатость породы по фото.lnk"
#define BUF 32768

static wchar_t dir[BUF], py[BUF], script[BUF], exe[BUF];

static BOOL exists(const wchar_t *p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }

static BOOL make_link(const wchar_t *lnk)
{
    IShellLinkW *sl = NULL;
    IPersistFile *pf = NULL;
    BOOL ok = FALSE;
    if (FAILED(CoCreateInstance(&CLSID_ShellLink, NULL, CLSCTX_INPROC_SERVER, &IID_IShellLinkW, (void **)&sl)))
        return FALSE;
    IShellLinkW_SetPath(sl, exe);
    IShellLinkW_SetWorkingDirectory(sl, dir);
    IShellLinkW_SetIconLocation(sl, exe, 0);
    IShellLinkW_SetDescription(sl, TITLE);
    if (SUCCEEDED(IShellLinkW_QueryInterface(sl, &IID_IPersistFile, (void **)&pf))) {
        ok = SUCCEEDED(IPersistFile_Save(pf, lnk, TRUE));
        IPersistFile_Release(pf);
    }
    IShellLinkW_Release(sl);
    return ok;
}

static void offer_shortcut(void)
{
    wchar_t desk[MAX_PATH], lnk[BUF], flag[BUF], local[MAX_PATH];
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_DESKTOPDIRECTORY, NULL, 0, desk)))
        return;
    swprintf(lnk, BUF, L"%ls%ls", desk, LINK_NAME);
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (exists(lnk)) {
        make_link(lnk);              /* папку могли перенести — обновляем ярлык молча */
        return;
    }
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, local)))
        return;
    swprintf(flag, BUF, L"%ls\\KuskovatostFoto", local);
    CreateDirectoryW(flag, NULL);
    wcscat(flag, L"\\shortcut_asked");
    if (exists(flag))
        return;
    CloseHandle(CreateFileW(flag, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL));
    if (MessageBoxW(NULL, L"Создать ярлык программы на рабочем столе?", TITLE,
                    MB_YESNO | MB_ICONQUESTION) == IDYES)
        if (!make_link(lnk))
            MessageBoxW(NULL, L"Ярлык создать не удалось. Программу можно запускать файлом Kuskovatost.exe.",
                        TITLE, MB_ICONWARNING);
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR args, int show)
{
    wchar_t *cmd, *slash;
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    DWORD code = 0;
    size_t n;

    GetModuleFileNameW(NULL, exe, BUF);
    wcscpy(dir, exe);
    slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    swprintf(py, BUF, L"%ls\\runtime\\pythonw.exe", dir);
    swprintf(script, BUF, L"%ls\\app\\run.py", dir);
    if (!exists(py) || !exists(script)) {
        MessageBoxW(NULL,
            L"Не найдены файлы программы (папки runtime и app).\n\n"
            L"Скорее всего, программа запущена прямо из архива. Распакуйте архив целиком "
            L"(правая кнопка мыши → «Извлечь всё…») и запустите Kuskovatost.exe "
            L"из распакованной папки.",
            TITLE, MB_ICONERROR);
        return 1;
    }
    offer_shortcut();

    n = wcslen(py) + wcslen(script) + wcslen(args) + 32;
    cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, n * sizeof(wchar_t));
    swprintf(cmd, n, L"\"%ls\" -E -s -X utf8 \"%ls\" %ls", py, script, args);
    if (!CreateProcessW(py, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        wchar_t msg[512];
        swprintf(msg, 512, L"Не удалось запустить программу (код ошибки Windows %lu).\n\n"
                 L"Возможно, запуск заблокирован антивирусом.", GetLastError());
        MessageBoxW(NULL, msg, TITLE, MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    /* 0 — обычное закрытие, 3 — ошибка уже показана самой программой */
    if (code != 0 && code != 3) {
        wchar_t msg[BUF];
        swprintf(msg, BUF, L"Программа неожиданно закрылась (код %lu).\n\n"
                 L"Журнал: %ls\\logs\\foto.log\n(или %%LOCALAPPDATA%%\\KuskovatostFoto\\logs\\foto.log)\n\n"
                 L"Пришлите этот файл разработчику.", code, dir);
        MessageBoxW(NULL, msg, TITLE, MB_ICONERROR);
    }
    return (int)code;
}
