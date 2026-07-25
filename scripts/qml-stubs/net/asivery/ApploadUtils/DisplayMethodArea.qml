import QtQuick 2.15

// Desktop stub for the device-only ApploadUtils e-ink refresh control.
Item {
    enum Methods {
        Fast,
        Quality
    }
    property int displayMethod: DisplayMethodArea.Fast
}
