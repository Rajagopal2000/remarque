import QtQuick 2.15

// Desktop stub for the device-only ApploadUtils e-ink refresh control.
// Enum mirrors the real appload API (fastest to highest quality).
Item {
    enum Method {
        UFast,
        Fast,
        Animate,
        Content,
        UI
    }
    property int displayMethod: DisplayMethodArea.Content
}
