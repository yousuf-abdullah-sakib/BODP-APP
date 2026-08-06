import "leaflet";

declare module "leaflet" {
  namespace Control {
    class Draw extends Control {
      constructor(options?: unknown);
    }
  }

  namespace Draw {
    const Event: {
      CREATED: string;
      EDITED: string;
      DELETED: string;
      DRAWSTART: string;
      DRAWSTOP: string;
      DRAWVERTEX: string;
      EDITSTART: string;
      EDITMOVE: string;
      EDITRESIZE: string;
      EDITVERTEX: string;
      EDITSTOP: string;
      DELETESTART: string;
      DELETESTOP: string;
    };
  }

  namespace DrawEvents {
    interface Created {
      layer: Layer;
      layerType: string;
    }
  }
}
